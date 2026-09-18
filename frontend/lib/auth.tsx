"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { ApiRequestError, apiGet, apiSend, setToken } from "./api";
import type { TokenResponse, UserProfile } from "./types";

interface AuthState {
  user: UserProfile | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (
    email: string,
    password: string,
    organizationName: string,
  ) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  const refresh = useCallback(async () => {
    try {
      setUser(await apiGet<UserProfile>("/v1/auth/me"));
    } catch (error) {
      // An expired or missing token is the normal signed-out state, not a
      // fault worth surfacing. Anything else is, so leave it visible.
      if (error instanceof ApiRequestError && error.status === 401) {
        setToken(null);
        setUser(null);
      } else {
        throw error;
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh().catch(() => setLoading(false));
  }, [refresh]);

  const adopt = useCallback((token: TokenResponse) => {
    setToken(token.access_token);
    setUser(token.user);
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      adopt(
        await apiSend<TokenResponse>(
          "/v1/auth/login",
          { email, password },
          "POST",
          true,
        ),
      );
    },
    [adopt],
  );

  const signup = useCallback(
    async (email: string, password: string, organizationName: string) => {
      adopt(
        await apiSend<TokenResponse>(
          "/v1/auth/signup",
          {
            email,
            password,
            organization_name: organizationName || undefined,
          },
          "POST",
          true,
        ),
      );
    },
    [adopt],
  );

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    router.push("/login");
  }, [router]);

  const value = useMemo(
    () => ({ user, loading, login, signup, logout, refresh }),
    [user, loading, login, signup, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return context;
}
