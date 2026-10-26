// d:\Python_exercise\项目2-智慧投研Agent\③项目代码\agentic_research\frontend\src\App.tsx
import React from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import useAuthStore from '@/utils/token'
import Login from '@/pages/Login'
import Register from '@/pages/Register'
import Dashboard from '@/pages/Dashboard'
import Report from '@/pages/Report'
import Layout from '@/components/Layout'

function App() {
  const token = useAuthStore((state: { token: string | null }) => state.token)

  return (
    <Routes>
      <Route path="/login" element={token ? <Navigate to="/" /> : <Login />} />
      <Route path="/register" element={token ? <Navigate to="/" /> : <Register />} />
      <Route
        path="/"
        element={token ? <Layout><Dashboard /></Layout> : <Navigate to="/login" />}
      />
      <Route
        path="/report/:taskId"
        element={token ? <Layout><Report /></Layout> : <Navigate to="/login" />}
      />
      <Route path="*" element={<Navigate to="/" />} />
    </Routes>
  )
}

export default App