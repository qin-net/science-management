"use client";

import { useState } from "react";

import { ErrorNotice } from "@/components/ui";
import { ApiFailure, sendJson } from "@/lib/api";
import type { Block, TranslationsResult } from "@/lib/types";

export type TranslationLanguage = "zh" | "en";

type Outcome = { page: number; remaining: number; missing: number };

/** Translates the untranslated blocks of one page; the server sends as many as its budget
 * allows and says how many are left, so a long page takes more than one click. */
export function TranslationPanel({
  base,
  page,
  blocks,
  translated,
  language,
  canTranslate,
  onLanguage,
  onTranslated,
}: {
  base: string;
  page: number;
  blocks: Block[];
  translated: Set<string>;
  language: TranslationLanguage;
  canTranslate: boolean;
  onLanguage: (language: TranslationLanguage) => void;
  onTranslated: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiFailure | null>(null);
  const [result, setResult] = useState<Outcome | null>(null);
  const pending = blocks.filter(
    (block) => block.page_no === page && block.text && !translated.has(block.id),
  );

  async function translate() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const out = await sendJson<TranslationsResult>(`${base}/translations`, "POST", {
        block_ids: pending.map((block) => block.id),
        language,
      });
      setResult({
        page,
        remaining: out.remaining.length,
        missing: out.missing.length,
      });
      await onTranslated();
    } catch (failure) {
      setError(failure as ApiFailure);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack" data-testid="translation-panel">
      <h2>段落翻译</h2>
      {canTranslate && (
        <p className="muted small" data-testid="translation-privacy">
          翻译时，这一页文本块的原文会发送给 Anthropic 的模型服务。译文由模型生成，请对照原文核对。
        </p>
      )}
      <div className="row">
        <select
          aria-label="译文语言"
          value={language}
          onChange={(event) => {
            setResult(null);
            onLanguage(event.target.value as TranslationLanguage);
          }}
          style={{ width: "auto" }}
        >
          <option value="zh">中文</option>
          <option value="en">English</option>
        </select>
        {canTranslate && (
          <button
            data-testid="translation-run"
            disabled={busy || pending.length === 0}
            onClick={() => void translate()}
          >
            {busy ? "翻译中…" : `翻译第 ${page} 页`}
          </button>
        )}
      </div>
      {canTranslate && pending.length === 0 && !busy && (
        <p className="muted small" data-testid="translation-done">
          这一页没有需要翻译的文本。
        </p>
      )}
      {result && result.page === page && result.remaining > 0 && (
        <p className="notice info small" data-testid="translation-remaining">
          本页还有 {result.remaining} 段未翻译，再点一次继续。
        </p>
      )}
      {result && result.page === page && result.missing > 0 && (
        <p className="notice warn small" data-testid="translation-missing">
          有 {result.missing} 段模型没有给出译文，可以再点一次重试。
        </p>
      )}
      {error && (
        <div data-testid="translation-error">
          <ErrorNotice error={error} />
        </div>
      )}
    </div>
  );
}
