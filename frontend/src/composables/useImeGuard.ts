import { ref } from 'vue'

/** Shared IME (input-method) composition guard for textareas that submit on Enter.
 *
 *  Bind ``@compositionstart`` / ``@compositionend`` to the returned handlers and
 *  check ``composing.value`` (or ``event.isComposing``) before submitting, so
 *  Enter used to confirm an IME candidate does not fire the message. */
export function useImeGuard() {
  const composing = ref(false)
  const onCompositionStart = () => { composing.value = true }
  const onCompositionEnd = () => { composing.value = false }
  return { composing, onCompositionStart, onCompositionEnd }
}
