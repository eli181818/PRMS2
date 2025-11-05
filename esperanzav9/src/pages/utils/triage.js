// triage.js
// Parse blood pressure string "120/80" into systolic and diastolic numbers
export function parseBp(bpStr) {
  if (!bpStr || typeof bpStr !== 'string') return { sys: null, dia: null }
  const m = bpStr.match(/(\d+)\s*\/\s*(\d+)/)
  if (!m) return { sys: null, dia: null }
  return { sys: Number(m[1]), dia: Number(m[2]) }
}

// Triage vital signs and return abnormal reasons
export function triageAbnormal({ hr, bp, spo2, temp }) {
  const reasons = []
  const { sys, dia } = parseBp(bp)

  // Check for abnormalities
  if (typeof hr === 'number') {
    if (hr < 50) reasons.push('Bradycardia (HR < 50)')
    if (hr > 100) reasons.push('Tachycardia (HR > 100)')
  }
  if (sys != null && dia != null) {
    if (sys >= 140 || dia >= 90) reasons.push('Hypertension (BP ≥ 140/90)')
  }
  if (typeof spo2 === 'number' && spo2 < 95) {
    reasons.push('Low SpO₂ (< 95%)')
  }
  if (typeof temp === 'number' && temp >= 38) {
    reasons.push('Fever (Temp ≥ 38°C)')
  }
  return { abnormal: reasons.length > 0, reasons }
}

// Generate next priority code like "E01", "E02", ..., "E99", cycling
export function nextPriorityCode() {
  const key = 'priorityCounter'
  const raw = Number(sessionStorage.getItem(key) || '0')
  const next = ((raw % 99) + 1) // 1..99
  sessionStorage.setItem(key, String(next))
  return `E${String(next).padStart(2, '0')}`
}