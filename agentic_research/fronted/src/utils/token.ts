import { create } from 'zustand'

const TOKEN_KEY = 'token'

const useAuthStore = create<{
  token: string | null
  setToken: (token: string) => void
  clearToken: () => void
}>((set) => ({
  token: localStorage.getItem(TOKEN_KEY),
  setToken: (token) => {
    localStorage.setItem(TOKEN_KEY, token)
    set({ token })
  },
  clearToken: () => {
    localStorage.removeItem(TOKEN_KEY)
    set({ token: null })
  },
}))

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
  useAuthStore.getState().clearToken()
}

export default useAuthStore