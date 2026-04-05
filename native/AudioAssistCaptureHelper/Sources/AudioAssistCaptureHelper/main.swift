// AudioAssistCaptureHelper — system-audio (and mix) capture via ScreenCaptureKit.
//
// CLI contract:
//   AudioAssistCaptureHelper stream \
//     --mode system|mix \
//     --pcm-fifo /tmp/audioassist-<session>.fifo \
//     --wav-out  /path/to/session/realtime_recording.wav \
//     [--sample-rate 16000] [--channels 1]
//
// stdout: NDJSON events (one JSON object per line)
// stderr: debug logs
// named pipe: raw float32 PCM (16 kHz, mono)
// wav file:   PCM16 WAV (16 kHz, mono)
//
// Lifecycle signals:
//   SIGUSR1 → pause  (stop writing to pipe/WAV, keep stream open)
//   SIGUSR2 → resume
//   SIGTERM / SIGINT → flush WAV, close everything, exit 0
//
// Mix mode adds AVAudioEngine mic capture.  Both streams are converted to
// 16 kHz / mono / float32, then mixed sample-by-sample (clamped to [-1, 1]).
// Mic capture failures are non-fatal: a "warning" event is emitted and the
// session continues with system audio only.

import Foundation
import AVFoundation
import CoreMedia
import ScreenCaptureKit

// MARK: - NDJSON event output

private func emit(_ dict: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: dict),
          let line = String(data: data, encoding: .utf8) else { return }
    print(line)
    fflush(stdout)
}

// MARK: - WAV writer

/// Writes PCM16 mono WAV incrementally; call close() to patch header sizes.
final class WAVWriter {
    private let fileHandle: FileHandle
    private var dataBytes: UInt32 = 0
    private let sampleRate: UInt32

    init?(path: String, sampleRate: UInt32) {
        self.sampleRate = sampleRate
        FileManager.default.createFile(atPath: path, contents: nil)
        guard let fh = FileHandle(forWritingAtPath: path) else { return nil }
        self.fileHandle = fh
        writeHeader()
    }

    private func writeHeader() {
        var h = Data()
        // RIFF
        h += "RIFF".data(using: .ascii)!
        h.appendLE32(36)               // placeholder: RIFF chunk size
        h += "WAVE".data(using: .ascii)!
        // fmt
        h += "fmt ".data(using: .ascii)!
        h.appendLE32(16)               // chunk size
        h.appendLE16(1)                // PCM
        h.appendLE16(1)                // mono
        h.appendLE32(sampleRate)
        h.appendLE32(sampleRate * 2)   // byte rate
        h.appendLE16(2)                // block align
        h.appendLE16(16)               // bits per sample
        // data
        h += "data".data(using: .ascii)!
        h.appendLE32(0)                // placeholder: data size
        fileHandle.write(h)
    }

    func write(floats: UnsafePointer<Float>, count: Int) {
        guard count > 0 else { return }
        var buf = [Int16](repeating: 0, count: count)
        for i in 0..<count {
            let s = max(-1.0, min(1.0, floats[i]))
            buf[i] = Int16(s * 32767.0)
        }
        buf.withUnsafeBytes { fileHandle.write(Data($0)) }
        dataBytes += UInt32(count * 2)
    }

    func close() {
        fileHandle.seek(toFileOffset: 4)
        var riffSize = (36 + dataBytes).littleEndian
        fileHandle.write(Data(bytes: &riffSize, count: 4))
        fileHandle.seek(toFileOffset: 40)
        var ds = dataBytes.littleEndian
        fileHandle.write(Data(bytes: &ds, count: 4))
        try? fileHandle.close()
    }
}

private extension Data {
    mutating func appendLE16(_ v: UInt16) {
        var x = v.littleEndian
        append(Data(bytes: &x, count: 2))
    }
    mutating func appendLE32(_ v: UInt32) {
        var x = v.littleEndian
        append(Data(bytes: &x, count: 4))
    }
}

// MARK: - Audio mixer (mix mode only)

/// Ring-buffer mixer for system + microphone audio.
/// Not thread-safe — must be accessed from a single serial queue (writeQueue).
final class AudioMixer {
    private var systemBuf: [Float] = []
    private var micBuf:    [Float] = []

    var systemCount: Int { systemBuf.count }
    var micCount:    Int { micBuf.count    }

    func appendSystem(_ samples: [Float]) { systemBuf += samples }
    func appendMic(_ samples: [Float])    { micBuf    += samples }

    /// Mix and drain all available samples, padding the shorter stream with zeros.
    func drainAll() -> [Float] {
        let count = max(systemBuf.count, micBuf.count)
        guard count > 0 else { return [] }
        var mixed = [Float](repeating: 0, count: count)
        for i in 0..<count {
            let s: Float = i < systemBuf.count ? systemBuf[i] : 0.0
            let m: Float = i < micBuf.count    ? micBuf[i]    : 0.0
            mixed[i] = max(-1.0, min(1.0, s + m))
        }
        systemBuf.removeAll(keepingCapacity: true)
        micBuf.removeAll(keepingCapacity: true)
        return mixed
    }
}

// MARK: - Audio processor / SCStream delegate

final class AudioProcessor: NSObject, SCStreamOutput, SCStreamDelegate {
    // Configuration
    let targetSampleRate: Double
    let wavWriter: WAVWriter?
    let mode: String

    // FIFO file descriptor (opened asynchronously)
    private var fifoFD: Int32 = -1
    private let fifoPath: String
    private var fifoReady = false

    // State (guarded by stateQueue)
    private let stateQueue = DispatchQueue(label: "audio.state")
    private var _paused  = false
    private var _stopped = false
    var paused:  Bool { stateQueue.sync { _paused  } }
    var stopped: Bool { stateQueue.sync { _stopped } }
    func setPaused(_ v: Bool)  { stateQueue.async { self._paused  = v } }
    func setStopped(_ v: Bool) { stateQueue.async { self._stopped = v } }

    // SCStream audio converter (lazily created on first buffer)
    private var converter: AVAudioConverter?
    private var srcFormat: AVAudioFormat?
    let dstFormat: AVAudioFormat

    // Mix mode
    private var mixBuffer: AudioMixer?
    private var micEngine: AVAudioEngine?

    // Stats
    private var _droppedFrames = 0
    var droppedFrames: Int { stateQueue.sync { _droppedFrames } }

    // Write queue — serialises all WAV / FIFO / mixer operations
    private let writeQueue = DispatchQueue(label: "audio.write", qos: .userInitiated)

    init(targetSampleRate: Int, wavWriter: WAVWriter?, fifoPath: String, mode: String) {
        self.targetSampleRate = Double(targetSampleRate)
        self.wavWriter = wavWriter
        self.fifoPath  = fifoPath
        self.mode      = mode
        self.dstFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: Double(targetSampleRate),
            channels: 1,
            interleaved: true
        )!
        super.init()

        if mode == "mix" {
            mixBuffer = AudioMixer()
        }

        // Open FIFO write-end in background (blocks until Python reader connects)
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            let fd = Darwin.open(fifoPath, O_WRONLY)
            if fd >= 0 {
                let flags = fcntl(fd, F_GETFL)
                _ = fcntl(fd, F_SETFL, flags | O_NONBLOCK)
                self.stateQueue.async { self.fifoFD = fd; self.fifoReady = true }
                fputs("debug: fifo connected\n", stderr)
            } else {
                fputs("warning: could not open fifo \(fifoPath)\n", stderr)
            }
        }

        // Mix mode: start mic capture immediately so it's ready alongside SCStream
        if mode == "mix" {
            startMicCapture()
        }
    }

    // MARK: SCStreamDelegate

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        emit(["event": "error", "message": error.localizedDescription])
        onFatalError?(error.localizedDescription)
    }

    /// Injected after stopSemaphore is created so unexpected stream stops trigger shutdown.
    var onFatalError: ((String) -> Void)?

    // MARK: SCStreamOutput

    func stream(
        _ stream: SCStream,
        didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of outputType: SCStreamOutputType
    ) {
        guard outputType == .audio, !paused, !stopped else { return }
        processSystemBuffer(sampleBuffer)
    }

    // MARK: - System audio processing (SCStream → convert → write/mix)

    private func processSystemBuffer(_ sb: CMSampleBuffer) {
        let numFrames = CMSampleBufferGetNumSamples(sb)
        guard numFrames > 0 else { return }
        guard let fmtDesc = CMSampleBufferGetFormatDescription(sb) else { return }

        // Lazily create the converter from the actual input format
        if converter == nil {
            guard let asbdPtr = CMAudioFormatDescriptionGetStreamBasicDescription(fmtDesc) else { return }
            var asbd = asbdPtr.pointee
            guard let inFmt = AVAudioFormat(streamDescription: &asbd) else { return }
            srcFormat = inFmt
            converter = AVAudioConverter(from: inFmt, to: dstFormat)
        }
        guard let conv = converter, let inFmt = srcFormat else { return }

        // Copy CMSampleBuffer audio into AVAudioPCMBuffer
        guard let inputBuf = copyToAVAudioPCMBuffer(
            sb, format: inFmt, frameCount: AVAudioFrameCount(numFrames)
        ) else { return }

        // Calculate output capacity
        let ratio = dstFormat.sampleRate / inFmt.sampleRate
        let outCapacity = AVAudioFrameCount(Double(numFrames) * ratio) + 128
        guard let outputBuf = AVAudioPCMBuffer(pcmFormat: dstFormat, frameCapacity: outCapacity) else { return }

        // Convert (sample-rate + channel reduction)
        var convError: NSError?
        var provided = false
        _ = conv.convert(to: outputBuf, error: &convError) { _, outStatus in
            if !provided {
                provided = true
                outStatus.pointee = .haveData
                return inputBuf
            }
            outStatus.pointee = .noDataNow
            return nil
        }
        guard convError == nil, let floatData = outputBuf.floatChannelData?[0] else { return }
        let frames = Int(outputBuf.frameLength)
        guard frames > 0 else { return }

        // Copy into a Swift array so the closure outlives the buffer
        let samples = Array(UnsafeBufferPointer(start: floatData, count: frames))

        writeQueue.async { [weak self] in
            guard let self = self, !self.stopped else { return }
            if let mixer = self.mixBuffer {
                mixer.appendSystem(samples)
                self.tryFlushMix()
            } else {
                self.writeSamples(samples)
            }
        }
    }

    // MARK: - Mix mode: microphone capture via AVAudioEngine

    private func startMicCapture() {
        let engine = AVAudioEngine()
        let inputNode = engine.inputNode
        let inputFormat = inputNode.outputFormat(forBus: 0)

        guard inputFormat.sampleRate > 0 else {
            fputs("warning: no microphone available (sampleRate = 0)\n", stderr)
            emit(["event": "warning", "reason": "mic_unavailable"])
            return
        }

        guard let conv = AVAudioConverter(from: inputFormat, to: dstFormat) else {
            fputs("warning: could not create mic audio converter\n", stderr)
            emit(["event": "warning", "reason": "mic_converter_failed"])
            return
        }

        // Capture a local copy for the tap closure (avoids retaining self strongly)
        let dstFmt = dstFormat

        inputNode.installTap(onBus: 0, bufferSize: 4096, format: inputFormat) { [weak self] buffer, _ in
            guard let self = self, !self.paused, !self.stopped else { return }
            let ratio = dstFmt.sampleRate / inputFormat.sampleRate
            let outCap = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 128
            guard let outBuf = AVAudioPCMBuffer(pcmFormat: dstFmt, frameCapacity: outCap) else { return }
            var provideOnce = false
            _ = conv.convert(to: outBuf, error: nil) { _, status in
                if !provideOnce { provideOnce = true; status.pointee = .haveData; return buffer }
                status.pointee = .noDataNow; return nil
            }
            guard let floats = outBuf.floatChannelData?[0], outBuf.frameLength > 0 else { return }
            let samples = Array(UnsafeBufferPointer(start: floats, count: Int(outBuf.frameLength)))
            self.writeQueue.async { [weak self] in
                guard let self = self, !self.stopped, let mb = self.mixBuffer else { return }
                mb.appendMic(samples)
                self.tryFlushMix()
            }
        }

        do {
            try engine.start()
            micEngine = engine
            fputs("debug: mic capture started (mix mode)\n", stderr)
        } catch {
            inputNode.removeTap(onBus: 0)
            fputs("warning: mic capture start failed: \(error.localizedDescription)\n", stderr)
            emit(["event": "warning", "reason": "mic_capture_failed",
                  "message": error.localizedDescription])
        }
    }

    private func stopMicEngine() {
        guard let engine = micEngine else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        micEngine = nil
        fputs("debug: mic engine stopped\n", stderr)
    }

    // MARK: - Mix flush (called on writeQueue)

    /// Flush the mixer when enough data has accumulated (≥ 512 samples = 32 ms).
    private func tryFlushMix() {
        guard let mixer = mixBuffer else { return }
        let maxAvailable = max(mixer.systemCount, mixer.micCount)
        guard maxAvailable >= 512 else { return }
        let mixed = mixer.drainAll()
        guard !mixed.isEmpty else { return }
        writeSamples(mixed)
    }

    // MARK: - Common write path (called on writeQueue)

    private func writeSamples(_ samples: [Float]) {
        let byteCount = samples.count * MemoryLayout<Float>.size
        samples.withUnsafeBufferPointer { ptr in
            guard let base = ptr.baseAddress else { return }

            // FIFO write (non-blocking; drop frame if buffer full)
            let fd = stateQueue.sync { fifoFD }
            if fd >= 0 {
                let written = Darwin.write(fd, base, byteCount)
                if written < 0 && errno != EAGAIN && errno != EWOULDBLOCK {
                    stateQueue.async { self._droppedFrames += 1 }
                }
            }

            // WAV write (int16)
            wavWriter?.write(floats: base, count: samples.count)
        }
    }

    // MARK: - CMSampleBuffer → AVAudioPCMBuffer

    private func copyToAVAudioPCMBuffer(
        _ sb: CMSampleBuffer,
        format: AVAudioFormat,
        frameCount: AVAudioFrameCount
    ) -> AVAudioPCMBuffer? {
        guard let dst = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameCount) else { return nil }
        dst.frameLength = frameCount

        var ablSize = 0
        CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sb,
            bufferListSizeNeededOut: &ablSize,
            bufferListOut: nil, bufferListSize: 0,
            blockBufferAllocator: nil, blockBufferMemoryAllocator: nil,
            flags: 0, blockBufferOut: nil
        )
        guard ablSize > 0 else { return nil }

        let ablMem = UnsafeMutableRawPointer.allocate(byteCount: ablSize, alignment: 16)
        defer { ablMem.deallocate() }
        let abl = ablMem.assumingMemoryBound(to: AudioBufferList.self)

        var blockBuf: CMBlockBuffer?
        guard CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sb,
            bufferListSizeNeededOut: nil,
            bufferListOut: abl, bufferListSize: ablSize,
            blockBufferAllocator: nil, blockBufferMemoryAllocator: nil,
            flags: kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment,
            blockBufferOut: &blockBuf
        ) == noErr else { return nil }

        let ablPtr = UnsafeMutableAudioBufferListPointer(abl)
        if format.isInterleaved {
            if ablPtr.count >= 1, let src = ablPtr[0].mData,
               let dstPtr = dst.floatChannelData?[0] {
                memcpy(dstPtr, src, Int(ablPtr[0].mDataByteSize))
            }
        } else {
            for ch in 0..<Int(format.channelCount) {
                guard ch < ablPtr.count,
                      let src = ablPtr[ch].mData,
                      let dstPtr = dst.floatChannelData?[ch] else { continue }
                memcpy(dstPtr, src, Int(ablPtr[ch].mDataByteSize))
            }
        }
        return dst
    }

    func closeFIFO() {
        // Stop mic engine first so its tap no longer posts to writeQueue
        stopMicEngine()

        // Drain any remaining mixed samples
        writeQueue.sync {
            if let mixer = self.mixBuffer {
                let remaining = mixer.drainAll()
                if !remaining.isEmpty { self.writeSamples(remaining) }
            }
        }

        // Close FIFO fd
        stateQueue.sync {
            if fifoFD >= 0 {
                Darwin.close(fifoFD)
                fifoFD = -1
            }
        }
    }
}

// MARK: - Argument parsing

private func parseArgs() -> (mode: String, pcmFifo: String, wavOut: String, sampleRate: Int) {
    var args = Array(CommandLine.arguments.dropFirst())
    guard args.first == "stream" else {
        fputs("Usage: AudioAssistCaptureHelper stream --mode system|mix --pcm-fifo <path> --wav-out <path>\n", stderr)
        exit(1)
    }
    args.removeFirst()

    var mode = "system", pcmFifo = "", wavOut = "", sampleRate = 16000
    var i = 0
    while i < args.count {
        switch args[i] {
        case "--mode":
            if i + 1 < args.count { mode = args[i + 1]; i += 1 }
        case "--pcm-fifo":
            if i + 1 < args.count { pcmFifo = args[i + 1]; i += 1 }
        case "--wav-out":
            if i + 1 < args.count { wavOut = args[i + 1]; i += 1 }
        case "--sample-rate":
            if i + 1 < args.count { sampleRate = Int(args[i + 1]) ?? 16000; i += 1 }
        default: break
        }
        i += 1
    }
    guard !pcmFifo.isEmpty, !wavOut.isEmpty else {
        emit(["event": "error", "reason": "missing_required_args"])
        exit(1)
    }
    return (mode, pcmFifo, wavOut, sampleRate)
}

// MARK: - Entry point

guard #available(macOS 13.0, *) else {
    emit(["event": "error", "reason": "screencapturekit_requires_macos_13_0"])
    exit(1)
}

let (mode, pcmFifo, wavOut, sampleRate) = parseArgs()
emit(["event": "starting", "mode": mode])

// WAV writer
guard let wavWriter = WAVWriter(path: wavOut, sampleRate: UInt32(sampleRate)) else {
    emit(["event": "error", "reason": "failed_to_create_wav", "path": wavOut])
    exit(1)
}

// Audio processor — opens FIFO asynchronously; starts mic if mode == "mix"
let processor = AudioProcessor(
    targetSampleRate: sampleRate,
    wavWriter: wavWriter,
    fifoPath: pcmFifo,
    mode: mode
)

// Get shareable content and start SCStream
let startSemaphore = DispatchSemaphore(value: 0)
var captureStream: SCStream?
var startErrorMessage: String?

SCShareableContent.getWithCompletionHandler { content, error in
    defer { startSemaphore.signal() }

    guard let content = content, error == nil else {
        startErrorMessage = error?.localizedDescription ?? "no_shareable_content"
        return
    }
    guard let display = content.displays.first else {
        startErrorMessage = "no_display_found"
        return
    }

    // Display-level filter captures all system audio from any app
    let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])

    let config = SCStreamConfiguration()
    config.capturesAudio = true
    config.excludesCurrentProcessAudio = true

    // macOS 13+ direct format configuration
    config.sampleRate = sampleRate
    config.channelCount = 1

    // Minimal video params (SCStream requires even for audio-only)
    config.width = 2
    config.height = 2
    config.minimumFrameInterval = CMTime(value: 1, timescale: 1)

    let s = SCStream(filter: filter, configuration: config, delegate: processor)
    do {
        try s.addStreamOutput(
            processor, type: .audio,
            sampleHandlerQueue: DispatchQueue(label: "audio.capture", qos: .userInitiated)
        )
        try s.startCapture()
        captureStream = s
    } catch {
        startErrorMessage = error.localizedDescription
    }
}

startSemaphore.wait()

if let err = startErrorMessage {
    emit(["event": "error", "reason": "stream_start_failed", "message": err])
    wavWriter.close()
    exit(1)
}

emit(["event": "started", "sample_rate": sampleRate, "channels": 1, "mode": mode])

// Signal handling (all on a background queue so main thread can block)
let sigQueue     = DispatchQueue(label: "signal.q")
let stopSemaphore = DispatchSemaphore(value: 0)

// Wire up fatal-error callback so an unexpected SCStream stop signals shutdown
processor.onFatalError = { _ in stopSemaphore.signal() }

signal(SIGUSR1, SIG_IGN)
signal(SIGUSR2, SIG_IGN)
signal(SIGTERM, SIG_IGN)
signal(SIGINT,  SIG_IGN)

let pauseSource = DispatchSource.makeSignalSource(signal: SIGUSR1, queue: sigQueue)
pauseSource.setEventHandler {
    processor.setPaused(true)
    emit(["event": "paused"])
}
pauseSource.resume()

let resumeSource = DispatchSource.makeSignalSource(signal: SIGUSR2, queue: sigQueue)
resumeSource.setEventHandler {
    processor.setPaused(false)
    emit(["event": "resumed"])
}
resumeSource.resume()

let termSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: sigQueue)
termSource.setEventHandler { stopSemaphore.signal() }
termSource.resume()

let intSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: sigQueue)
intSource.setEventHandler { stopSemaphore.signal() }
intSource.resume()

// Stats every 10 seconds
let statsTimer = DispatchSource.makeTimerSource(queue: sigQueue)
statsTimer.schedule(deadline: .now() + 10, repeating: 10)
statsTimer.setEventHandler {
    emit(["event": "stats", "dropped_frames": processor.droppedFrames])
}
statsTimer.resume()

// Block main thread until stop signal
stopSemaphore.wait()

// ── Shutdown ──────────────────────────────────────────────────────────────────

processor.setStopped(true)
statsTimer.cancel()

// Stop SCStream
let stopStreamSem = DispatchSemaphore(value: 0)
captureStream?.stopCapture { _ in stopStreamSem.signal() }
stopStreamSem.wait()

// Let write queue drain pending audio
Thread.sleep(forTimeInterval: 0.15)

processor.closeFIFO()
wavWriter.close()
emit(["event": "stopped"])
exit(0)
