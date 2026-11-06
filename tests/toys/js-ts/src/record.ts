import { field } from "./util";

export function parseRecord(data: Uint8Array): string | null {
  if (data.length < 5 || String.fromCharCode(data[0], data[1], data[2], data[3]) !== "REC1") return null;
  const text = Array.from(data.subarray(5), (b) => String.fromCharCode(b)).join("");
  return field(text.split(","), data[4]);
}
