/** Wake-word state machine for Discord spoken conversations.
 * Saying "Nova" alone arms the NEXT utterance; it never invents a question.
 * A short follow-up window after each successful reply allows real dialogue.
 */
export function stripWakeWord(text) {
  if (typeof text !== 'string') return null;
  const match = text.trim().match(
    /^(?:(?:hey|hallo|hi|servus|hör mal|sag mal)[\s,!.?-]*)?nova\b[\s,!.?:;-]*(.*)$/iu
  );
  return match ? match[1].trim() : null;
}

export class WakeDialogue {
  constructor({now = () => Date.now(), armMs = 18000, followupMs = 35000} = {}) {
    this.now = now;
    this.armMs = armMs;
    this.followupMs = followupMs;
    this.armedUntil = 0;
    this.followupUntil = 0;
  }

  interpret(transcript) {
    const words = typeof transcript === 'string' ? transcript.trim() : '';
    if (words.length < 2) return {kind: 'ignore', reason: 'empty'};
    const spoken = stripWakeWord(words);
    const now = this.now();

    if (spoken !== null) {
      this.followupUntil = 0;
      if (!spoken || /^[.!?…\s]+$/u.test(spoken)) {
        this.armedUntil = now + this.armMs;
        return {kind: 'armed', seconds: Math.ceil(this.armMs / 1000)};
      }
      this.armedUntil = 0;
      return {kind: 'question', text: spoken, source: 'wake'};
    }

    // The activation word may be delivered in a separate Discord clip.
    if (this.armedUntil > now) {
      this.armedUntil = 0;
      return {kind: 'question', text: words, source: 'armed'};
    }
    // The active owner can ask short follow-up questions without repeating Nova.
    if (this.followupUntil > now) {
      this.followupUntil = 0;
      return {kind: 'question', text: words, source: 'followup'};
    }
    return {kind: 'ignore', reason: 'no_wake'};
  }

  markReplied() {
    this.armedUntil = 0;
    this.followupUntil = this.now() + this.followupMs;
  }

  reset() {
    this.armedUntil = 0;
    this.followupUntil = 0;
  }

  status() {
    const now = this.now();
    return {
      armed: this.armedUntil > now,
      armed_seconds: Math.max(0, Math.ceil((this.armedUntil - now) / 1000)),
      followup_seconds: Math.max(0, Math.ceil((this.followupUntil - now) / 1000)),
    };
  }
}
