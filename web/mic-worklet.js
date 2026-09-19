// Collects mic samples into ~40 ms PCM16 chunks and posts them to the page.
class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.chunk = new Int16Array(640); // 40 ms at 16 kHz
    this.filled = 0;
  }

  process(inputs) {
    const input = inputs[0][0];
    if (!input) return true;
    for (let i = 0; i < input.length; i++) {
      const s = Math.max(-1, Math.min(1, input[i]));
      this.chunk[this.filled++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.filled === this.chunk.length) {
        this.port.postMessage(this.chunk.buffer.slice(0));
        this.filled = 0;
      }
    }
    return true;
  }
}

registerProcessor("mic-processor", MicProcessor);
