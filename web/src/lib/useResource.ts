"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiFailure } from "@/lib/api";

const POLL_MS = 2000;

export type Resource<T> = {
  data: T | null;
  error: ApiFailure | null;
  loading: boolean;
  reload: () => Promise<void>;
};

/**
 * Loads data and sends the user to /login when the session is gone. `pollWhile` keeps
 * refreshing only while it returns true, so idle pages do not hit the API.
 */
export function useResource<T>(
  load: () => Promise<T>,
  key: string,
  pollWhile?: (data: T) => boolean,
): Resource<T> {
  const router = useRouter();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiFailure | null>(null);
  const [loading, setLoading] = useState(true);
  const loadRef = useRef(load);
  const pollRef = useRef(pollWhile);
  loadRef.current = load;
  pollRef.current = pollWhile;

  const reload = useCallback(async () => {
    try {
      setData(await loadRef.current());
      setError(null);
    } catch (failure) {
      if (failure instanceof ApiFailure && failure.status === 401) {
        router.replace("/login");
        return;
      }
      setError(failure instanceof ApiFailure ? failure : new ApiFailure(0, "unknown", String(failure)));
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    setData(null);
    setLoading(true);
    void reload();
  }, [key, reload]);

  useEffect(() => {
    if (data === null || !pollRef.current?.(data)) {
      return;
    }
    const timer = setTimeout(() => void reload(), POLL_MS);
    return () => clearTimeout(timer);
  }, [data, reload]);

  return { data, error, loading, reload };
}
