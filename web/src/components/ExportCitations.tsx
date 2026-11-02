"use client";

import { useState } from "react";

import { ErrorNotice } from "@/components/ui";
import { ApiFailure, fetchFile } from "@/lib/api";

const FORMATS = [
  { format: "bibtex", label: "导出 BibTeX", file: "references.bib" },
  { format: "ris", label: "导出 RIS", file: "references.ris" },
] as const;

export function ExportCitations({
  projectId,
  paperCount,
}: {
  projectId: string;
  paperCount: number;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiFailure | null>(null);
  const [skipped, setSkipped] = useState(0);

  async function download(format: string, file: string) {
    setBusy(true);
    setError(null);
    setSkipped(0);
    try {
      const { blob, headers } = await fetchFile(
        `/projects/${projectId}/papers/export?format=${format}`,
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = file;
      link.click();
      URL.revokeObjectURL(url);
      setSkipped(Number(headers.get("X-Export-Skipped") ?? 0) || 0);
    } catch (failure) {
      setError(failure as ApiFailure);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack" data-testid="export-citations">
      <div className="row">
        {FORMATS.map(({ format, label, file }) => (
          <button
            key={format}
            type="button"
            data-testid={`export-${format}`}
            disabled={busy || paperCount === 0}
            onClick={() => download(format, file)}
          >
            {label}
          </button>
        ))}
        {paperCount === 0 && (
          <span className="muted small">没有可导出的内容</span>
        )}
      </div>
      {skipped > 0 && (
        <span className="muted small" data-testid="export-skipped">
          有 {skipped} 篇文献没有标题，未导出
        </span>
      )}
      {error && <ErrorNotice error={error} />}
    </div>
  );
}
