/**
 * OCR service — upload an image of a handwritten formula → get LaTeX back.
 */

import apiClient from "./api";
import type { OcrResponseBody } from "./types";

export async function recogniseFormula(
    image: File | Blob
): Promise<OcrResponseBody> {
    const form = new FormData();
    form.append("image", image);

    const { data } = await apiClient.post<OcrResponseBody>("/ocr", form, {
        headers: { "Content-Type": "multipart/form-data" },
    });
    return data;
}
