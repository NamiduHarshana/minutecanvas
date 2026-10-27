// AudioWorklet: mixed input (mic, or mic + tab) -> mono -> 24 kHz -> PCM16 chunks of 100 ms.
// Resampling: each output sample = average of the input samples in its window
// (a box low-pass, enough for speech) — works for 44.1 kHz and 48 kHz devices.
class PcmCapture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const target = (options.processorOptions && options.processorOptions.targetRate) || 24000;
    this.ratio = sampleRate / target;          // input samples per output sample
    this.half = this.ratio / 2;
    this.chunkLen = Math.round(target / 10);    // 100 ms
    this.buf = new Float32Array(0);             // pending input samples
    this.pos = this.half;                       // fractional input index of the next output sample
    this.out = new Int16Array(this.chunkLen);
    this.outN = 0;
    this.sumSq = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0 || !input[0]) return true;
    const n = input[0].length;
    const merged = new Float32Array(this.buf.length + n);
    merged.set(this.buf, 0);
    const chs = input.length;
    for (let i = 0; i < n; i++) {
      let s = 0;
      for (let c = 0; c < chs; c++) s += input[c][i];
      merged[this.buf.length + i] = s / chs;
    }
    let buf = merged;

    while (this.pos + this.half + 1 < buf.length) {
      const lo = Math.max(0, Math.ceil(this.pos - this.half));
      const hi = Math.min(buf.length - 1, Math.floor(this.pos + this.half));
      let sum = 0;
      for (let k = lo; k <= hi; k++) sum += buf[k];
      let v = sum / (hi - lo + 1);
      if (v > 1) v = 1;
      else if (v < -1) v = -1;
      this.sumSq += v * v;
      this.out[this.outN++] = v < 0 ? v * 0x8000 : v * 0x7fff;
      if (this.outN === this.chunkLen) {
        const level = Math.sqrt(this.sumSq / this.chunkLen);
        const pcm = this.out;
        this.port.postMessage({ pcm, level }, [pcm.buffer]);
        this.out = new Int16Array(this.chunkLen);
        this.outN = 0;
        this.sumSq = 0;
      }
      this.pos += this.ratio;
    }
    const drop = Math.max(0, Math.floor(this.pos - this.half) - 1);
    if (drop > 0) {
      this.buf = buf.slice(drop);
      this.pos -= drop;
    } else {
      this.buf = buf;
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCapture);
