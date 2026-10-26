// d:\Python_exercise\项目2-智慧投研Agent\③项目代码\agentic_research\frontend\src\api\auth.ts
import request from './axios'

interface User {
  id: string
  username: string
  email: string
  created_at: string
}

interface TokenResponse {
  access_token: string
  token_type: string
  user: User
}

interface LoginData {
  username: string
  password: string
}

interface RegisterData extends LoginData {
  email: string
}

export const authApi = {
  login: async (data: LoginData): Promise<TokenResponse> => {
    const params = new URLSearchParams()
    params.append('username', data.username)
    params.append('password', data.password)
    
    const res = await request.post('/login', params, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    })
    return res as unknown as TokenResponse
  },

  register: async (data: RegisterData): Promise<TokenResponse> => {
    const res = await request.post('/register', data)
    return res as unknown as TokenResponse
  },

  getMe: async (): Promise<User> => {
    const res = await request.get('/me')
    return res as unknown as User
  },
}