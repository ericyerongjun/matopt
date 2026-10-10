import apiClient from "./api.ts";
import type { ProviderCatalog } from "../types/protocol";

export async function fetchProviders(): Promise<ProviderCatalog> {
    const { data } = await apiClient.get<ProviderCatalog>("/providers");
    return data;
}
