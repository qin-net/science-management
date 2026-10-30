"use client";

import { useEffect, useRef, useState } from "react";

import type { Block } from "@/lib/types";

type PdfDocument = import("pdfjs-dist").PDFDocumentProxy;
type LoadingTask = import("pdfjs-dist").PDFDocumentLoadingTask;
type RenderTask = import("pdfjs-dist").RenderTask;

const SCALE = 1.4;

async function openDocument(url: string): Promise<LoadingTask> {
  const pdfjs = await import("pdfjs-dist");
  pdfjs.GlobalWorkerOptions.workerSrc = new URL(
    "pdfjs-dist/build/pdf.worker.min.mjs",
    import.meta.url,
  ).toString();
  return pdfjs.getDocument({ url, withCredentials: true });
}

/**
 * Renders one page. The highlight is positioned in percentages of the rendered page: the
 * bbox is normalized to the displayed page, the same frame as a PDF.js viewport, so crop
 * boxes and /Rotate need no conversion here.
 */
export function PdfView({
  url,
  page,
  highlight,
  onPageChange,
}: {
  url: string;
  page: number;
  highlight: Block | null;
  onPageChange: (page: number) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [document, setDocument] = useState<PdfDocument | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [renderedPage, setRenderedPage] = useState<number | null>(null);

  useEffect(() => {
    let current = true;
    let loading: LoadingTask | null = null;
    setDocument(null);
    setFailed(null);
    setRenderedPage(null);
    openDocument(url)
      .then(async (task) => {
        if (!current) {
          void task.destroy();
          return;
        }
        loading = task;
        const loaded = await task.promise;
        if (current) {
          setDocument(loaded);
        }
      })
      .catch((error: unknown) => {
        if (current) {
          setFailed(error instanceof Error ? error.name : "PDF 加载失败");
        }
      });
    return () => {
      current = false;
      void loading?.destroy();
    };
  }, [url]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!document || !canvas) {
      return;
    }
    let task: RenderTask | null = null;
    let current = true;
    setRenderedPage(null);
    void (async () => {
      const pdfPage = await document.getPage(page);
      if (!current) {
        return;
      }
      const viewport = pdfPage.getViewport({ scale: SCALE });
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      task = pdfPage.render({ canvas, viewport });
      try {
        await task.promise;
        if (current) {
          setRenderedPage(page);
        }
      } catch (error) {
        // A newer page interrupts this render; only real failures are worth showing.
        if ((error as Error).name !== "RenderingCancelledException" && current) {
          setFailed((error as Error).name);
        }
      }
    })();
    return () => {
      current = false;
      task?.cancel();
    };
  }, [document, page]);

  if (failed) {
    return (
      <div className="notice error" data-testid="pdf-error">
        <strong>PDF 无法在阅读器中打开</strong>
        <span className="mono muted">{failed}</span>
      </div>
    );
  }

  const box = highlight?.page_no === page ? highlight.bbox : null;
  return (
    <>
      <div className="pdf-toolbar">
        <button disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
          上一页
        </button>
        <span data-testid="page-indicator">
          第 {page} / {document?.numPages ?? "…"} 页
        </span>
        <button
          disabled={!document || page >= document.numPages}
          onClick={() => onPageChange(page + 1)}
        >
          下一页
        </button>
      </div>
      {!document && <p className="muted">正在加载 PDF…</p>}
      <div
        className="pdf-page"
        data-testid="pdf-page"
        data-rendered-page={renderedPage ?? undefined}
      >
        <canvas ref={canvasRef} />
        {box && renderedPage === page && (
          <div
            className="pdf-highlight"
            data-testid="pdf-highlight"
            style={{
              left: `${box[0] * 100}%`,
              top: `${box[1] * 100}%`,
              width: `${(box[2] - box[0]) * 100}%`,
              height: `${(box[3] - box[1]) * 100}%`,
            }}
          />
        )}
      </div>
    </>
  );
}
