// Planted bug for the CI scenarios: the field index isn't checked.
export function field(fields: string[], index: number): string {
  return fields[index].trim();
}
