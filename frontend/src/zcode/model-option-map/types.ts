/* Vendored whole file from ZCode 29628c9acdb81b703bbd4080c207a0e7ce5e276e: packages/model-option-map/src/types.ts
 * Apache-2.0; original notices retained. */
export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonObject | readonly JsonValue[];

export interface JsonObject {
  readonly [key: string]: JsonValue;
}

export type RestrictedCelValue = string | number;
export type ModelOptionName = "reasoningLevel" | "maxOutputTokens";

export interface RestrictedCelProgram {
  readonly source: string;
  evaluate(input: RestrictedCelValue): JsonValue;
}

export interface ModelOptionMapProgram {
  readonly source: string;
  evaluate(input: RestrictedCelValue): JsonObject;
}

export class RestrictedCelError extends Error {
  readonly offset: number;

  constructor(message: string, offset: number) {
    super(`${message} at offset ${offset}`);
    this.name = "RestrictedCelError";
    this.offset = offset;
  }
}

export class ModelOptionMapError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ModelOptionMapError";
  }
}
