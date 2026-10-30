"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { ErrorNotice } from "@/components/ui";
import { ApiFailure, sendJson } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [error, setError] = useState<ApiFailure | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const body: Record<string, FormDataEntryValue | null> = {
      email: form.get("email"),
      password: form.get("password"),
    };
    if (mode === "register") {
      body.display_name = form.get("display_name");
    }
    setBusy(true);
    setError(null);
    try {
      await sendJson(`/auth/${mode}`, "POST", body);
      router.replace("/");
    } catch (failure) {
      setError(failure as ApiFailure);
      setBusy(false);
    }
  }

  return (
    <main className="panel auth-card">
      <h1>{mode === "login" ? "登录 ResearchOS" : "注册账号"}</h1>
      <form className="stack" onSubmit={submit}>
        {mode === "register" && (
          <label>
            显示名称
            <input name="display_name" required maxLength={200} autoComplete="name" />
          </label>
        )}
        <label>
          邮箱
          <input name="email" type="email" required autoComplete="email" />
        </label>
        <label>
          密码{mode === "register" && "（至少 10 位）"}
          <input
            name="password"
            type="password"
            required
            minLength={mode === "register" ? 10 : undefined}
            autoComplete={mode === "register" ? "new-password" : "current-password"}
          />
        </label>
        {error && <ErrorNotice error={error} />}
        <button className="primary" disabled={busy}>
          {mode === "login" ? "登录" : "注册"}
        </button>
      </form>
      <button
        onClick={() => {
          setMode(mode === "login" ? "register" : "login");
          setError(null);
        }}
      >
        {mode === "login" ? "没有账号？去注册" : "已有账号？去登录"}
      </button>
    </main>
  );
}
