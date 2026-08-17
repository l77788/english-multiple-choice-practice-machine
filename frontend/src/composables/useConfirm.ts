import { inject, provide, ref, type Ref } from 'vue'

export interface ConfirmOptions {
  title: string
  message?: string
  confirmText?: string
  cancelText?: string
  danger?: boolean
}

export interface ConfirmState {
  visible: Ref<boolean>
  options: Ref<ConfirmOptions | null>
  resolve: Ref<((value: boolean) => void) | null>
}

const KEY = Symbol('confirm')

export function provideConfirm(): ConfirmState {
  const state: ConfirmState = {
    visible: ref(false),
    options: ref(null),
    resolve: ref(null),
  }
  provide(KEY, state)
  return state
}

export function useConfirm() {
  const state = inject<ConfirmState>(KEY)
  if (!state) {
    throw new Error('useConfirm must be used inside a component under provideConfirm')
  }
  return (options: ConfirmOptions) =>
    new Promise<boolean>((resolve) => {
      state.options.value = options
      state.resolve.value = resolve
      state.visible.value = true
    })
}
