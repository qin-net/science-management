"use client";

import Link from "next/link";
import { type FormEvent, useState } from "react";

import { ErrorNotice } from "@/components/ui";
import { ApiFailure, sendJson } from "@/lib/api";
import type { Answer, Paper } from "@/lib/types";

const UNAVAILABLE_REASONS: Record<string, string> = {
  no_file: "没有 PDF",
  not_parsed: "尚未解析",
  no_text: "没有解析出文本",
};

/** Every string from the model or the papers is rendered as text, never as markup. */
export function AskPanel({ projectId, papers }: { projectId: string; papers: Paper[] }) {
  const readable = papers.filter((paper) => paper.current_version?.current_parse_job_id);
  const [chosen, setChosen] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiFailure | null>(null);
  const [answer, setAnswer] = useState<Answer | null>(null);

  function toggle(id: string) {
    setChosen((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setAnswer(null);
    try {
      setAnswer(
        await sendJson<Answer>(`/projects/${projectId}/ask`, "POST", {
          question,
          paper_ids: chosen.length > 0 ? chosen : undefined,
        }),
      );
    } catch (failure) {
      setError(failure as ApiFailure);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" data-testid="ask-panel">
      <h2>向文献提问</h2>
      <p className="muted small" data-testid="ask-privacy">
        提问时，你的问题和所选文献的全部解析文本会发送给 Anthropic 的模型服务。回答由模型生成，
        可能出错；每段回答下方的原文来自文献本身，请自己核对。
      </p>
      <form className="stack" onSubmit={submit}>
        {readable.length > 0 && (
          <fieldset className="stack" style={{ border: "none", padding: 0, margin: 0 }}>
            <legend className="muted small">范围（不选则使用全部已解析的文献）</legend>
            {readable.map((paper) => (
              <label key={paper.id} className="row" style={{ width: "auto" }}>
                <input
                  type="checkbox"
                  style={{ width: "auto" }}
                  checked={chosen.includes(paper.id)}
                  onChange={() => toggle(paper.id)}
                />
                <span>{paper.title ?? "（标题未知）"}</span>
              </label>
            ))}
          </fieldset>
        )}
        <label>
          问题
          <textarea
            data-testid="ask-question"
            value={question}
            maxLength={2000}
            onChange={(event) => setQuestion(event.target.value)}
          />
        </label>
        <div className="row">
          <button className="primary" data-testid="ask-submit" disabled={busy || !question.trim()}>
            {busy ? "等待回答…" : "提问"}
          </button>
        </div>
      </form>
      {error && (
        <div data-testid="ask-error">
          <ErrorNotice error={error} />
        </div>
      )}
      {answer && <AnswerView answer={answer} projectId={projectId} />}
    </section>
  );
}

export function AnswerView({ answer, projectId }: { answer: Answer; projectId: string }) {
  return (
    <div className="stack" data-testid="ask-answer">
      {!answer.answerable && (
        <div className="empty" data-testid="ask-unanswerable">
          所选文献里没有找到依据
        </div>
      )}
      {answer.segments.map((segment, index) => (
        <div className="list-item" key={index} data-testid="ask-segment">
          <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{segment.text}</p>
          {segment.supported ? (
            <details data-testid="ask-evidence">
              <summary>有依据（{segment.evidence.length} 处原文）</summary>
              <ul className="list">
                {segment.evidence.map((item) => (
                  <li className="list-item" key={item.block_id}>
                    <span className="muted small">
                      {item.paper_title ?? "（标题未知）"} · 第 {item.page_no} 页
                    </span>
                    <blockquote
                      style={{ margin: 0, whiteSpace: "pre-wrap" }}
                      data-testid="ask-evidence-text"
                    >
                      {item.text}
                    </blockquote>
                    <Link
                      data-testid="ask-evidence-link"
                      href={`/projects/${projectId}/papers/${item.paper_id}?block=${item.block_id}`}
                    >
                      在阅读器中定位
                    </Link>
                  </li>
                ))}
              </ul>
            </details>
          ) : (
            <span className="badge queued" data-testid="ask-unsupported">
              无出处
            </span>
          )}
        </div>
      ))}
      {answer.dropped_citations > 0 && (
        <span className="muted small" data-testid="ask-dropped">
          模型给出了 {answer.dropped_citations} 个无效的出处编号，已丢弃。
        </span>
      )}
      {answer.unavailable.length > 0 && (
        <span className="muted small" data-testid="ask-unavailable">
          未用于回答：
          {answer.unavailable
            .map(
              (item) =>
                `${item.paper_title ?? "（标题未知）"}（${UNAVAILABLE_REASONS[item.reason] ?? item.reason}）`,
            )
            .join("，")}
        </span>
      )}
      <span className="muted small">
        模型：{answer.model}
        {answer.usage.input_tokens !== null && ` · 输入 ${answer.usage.input_tokens} token`}
        {answer.usage.output_tokens !== null && ` · 输出 ${answer.usage.output_tokens} token`}
      </span>
    </div>
  );
}
