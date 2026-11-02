"use client";

import { useState } from "react";

import { AnswerView } from "@/components/AskPanel";
import { ErrorNotice } from "@/components/ui";
import { ApiFailure, sendJson } from "@/lib/api";
import type { Answer } from "@/lib/types";

/** The note text: one line per paragraph, pages after the claim, unsupported ones flagged. */
export function summaryToNote(answer: Answer): string {
  const lines = ["核心贡献总结（模型生成，请核对原文）"];
  for (const segment of answer.segments) {
    if (!segment.supported) {
      lines.push(`【无出处】${segment.text}`);
      continue;
    }
    const pages = [...new Set(segment.evidence.map((item) => item.page_no))].sort((a, b) => a - b);
    lines.push(`${segment.text}（第 ${pages.join("、")} 页）`);
  }
  return lines.join("\n");
}

export function SummaryPanel({
  projectId,
  paperId,
  canAsk,
  canWrite,
  onSaved,
}: {
  projectId: string;
  paperId: string;
  canAsk: boolean;
  canWrite: boolean;
  onSaved: () => void;
}) {
  const [language, setLanguage] = useState<"zh" | "en">("zh");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiFailure | null>(null);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [saved, setSaved] = useState(false);

  async function generate() {
    setBusy(true);
    setError(null);
    setAnswer(null);
    setSaved(false);
    try {
      setAnswer(
        await sendJson<Answer>(`/projects/${projectId}/papers/${paperId}/summary`, "POST", {
          language,
        }),
      );
    } catch (failure) {
      setError(failure as ApiFailure);
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!answer) {
      return;
    }
    setError(null);
    try {
      await sendJson(`/projects/${projectId}/notes`, "POST", {
        body: summaryToNote(answer),
        paper_id: paperId,
      });
      setSaved(true);
      onSaved();
    } catch (failure) {
      setError(failure as ApiFailure);
    }
  }

  if (!canAsk) {
    return null;
  }
  return (
    <div className="stack" data-testid="summary-panel">
      <h2>核心贡献总结</h2>
      <p className="muted small" data-testid="summary-privacy">
        生成时，这篇文献的全部解析文本会发送给 Anthropic 的模型服务。总结由模型生成，请对照原文核对。
      </p>
      <div className="row">
        <select
          aria-label="总结语言"
          value={language}
          onChange={(event) => setLanguage(event.target.value as "zh" | "en")}
          style={{ width: "auto" }}
        >
          <option value="zh">中文</option>
          <option value="en">English</option>
        </select>
        <button data-testid="summary-generate" disabled={busy} onClick={() => void generate()}>
          {busy ? "生成中…" : "生成核心贡献总结"}
        </button>
      </div>
      {error && (
        <div data-testid="summary-error">
          <ErrorNotice error={error} />
        </div>
      )}
      {answer && (
        <div data-testid="summary-result">
          <AnswerView answer={answer} projectId={projectId} />
          {answer.segments.length > 0 && canWrite && (
            <div className="row">
              <button data-testid="summary-save" disabled={saved} onClick={() => void save()}>
                {saved ? "已存为笔记" : "存为笔记"}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
