/* Vendored whole file from ZCode 29628c9acdb81b703bbd4080c207a0e7ce5e276e: packages/ui/src/hooks/useServices.tsx
 * Apache-2.0; original notices retained. MatOpt host adaptations are identified below. */
/**
 * useServices —— 通过 React Context 提供 IServiceAccessor
 *
 * 替代 props drilling，组件通过 useServices() 直接获取服务。
 */
import { createContext, useContext, type ReactNode } from "react";
// MatOpt host: optional service types only; no desktop RPC implementation.
import type { IServiceAccessor } from "@/adapters/serviceTypes.js";

const ServiceContext = createContext<IServiceAccessor | null>(null);

export function ServiceProvider({
  services,
  children,
}: {
  services: IServiceAccessor;
  children: ReactNode;
}) {
  return <ServiceContext.Provider value={services}>{children}</ServiceContext.Provider>;
}

export function useServices(): IServiceAccessor {
  const ctx = useContext(ServiceContext);
  if (!ctx) {
    throw new Error("useServices 必须在 ServiceProvider 内使用");
  }
  return ctx;
}

export function useOptionalServices(): IServiceAccessor | null {
  return useContext(ServiceContext);
}
