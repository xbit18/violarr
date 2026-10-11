export const units = { minutes: 60, hours: 3600, days: 86400 } as const
export type IntervalUnit = keyof typeof units

// Decimal arithmetic avoids admitting fractional seconds through floating-point rounding.
export function intervalSeconds(value: string, unit: IntervalUnit): number | null {
  const match = /^(\d*)(?:\.(\d*))?(?:e([+-]?\d+))?$/i.exec(value.trim())
  if (!match || !(match[1] + (match[2] ?? ''))) return null
  const fraction = match[2] ?? ''
  const exponent = Number(match[3] ?? 0) - fraction.length
  if (Math.abs(exponent) > 100 || value.length > 120) return null
  let numerator = BigInt(match[1] + fraction) * BigInt(units[unit])
  const divisor = exponent < 0 ? 10n ** BigInt(-exponent) : 1n
  if (exponent > 0) numerator *= 10n ** BigInt(exponent)
  if (numerator % divisor !== 0n) return null
  const seconds = Number(numerator / divisor)
  return Number.isInteger(seconds) && seconds >= 60 && seconds <= 604800 ? seconds : null
}
