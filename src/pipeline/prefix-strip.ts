/**
 * After a converted prefill, the model repeats the prefill text before
 * continuing. Anthropic's contract is that the reply starts after the prefill,
 * so strip the echoed copy. Text is held back only while it could still be
 * the start of the prefill.
 */
export class PrefixStripper {
  private buf = "";
  private done = false;
  private readonly prefix: string;

  constructor(prefix: string) {
    this.prefix = prefix;
  }

  push(text: string): string {
    if (this.done) return text;
    this.buf += text;
    const lead = this.buf.trimStart();
    if (lead.startsWith(this.prefix)) {
      this.done = true;
      return lead.slice(this.prefix.length);
    }
    if (this.prefix.startsWith(lead)) return ""; // still undecided
    // The model didn't echo the prefill; pass everything through.
    this.done = true;
    const out = this.buf;
    this.buf = "";
    return out;
  }

  /** End of text. A reply that is only part of the prefill continues it with nothing. */
  flush(): string {
    if (this.done) return "";
    this.done = true;
    const lead = this.buf.trimStart();
    return this.prefix.startsWith(lead) ? "" : this.buf;
  }
}
