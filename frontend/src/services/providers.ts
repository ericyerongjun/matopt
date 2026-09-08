import apiClient from "./api";
import type { ProviderCatalog } from "../types/protocol";

export async function fetchProviders(): Promise<ProviderCatalog> {
    const { data } = await apiClient.get<ProviderCatalog>("/providers");
    return data;
}
