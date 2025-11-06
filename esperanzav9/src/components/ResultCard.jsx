import React from 'react';

export default function ResultCard({ label, value, unit }) {
  return (
    <div className="mx-auto w-full max-w-md rounded-2xl border bg-white p-6 text-center shadow">
      <div className="text-[#406E65] font-semibold">{label}</div>
      <div className="mt-3 text-5xl font-extrabold tabular-nums text-[#406E65]">{value}</div>
      {unit && <div className="mt-1 text-sm text-[#406E65]">{unit}</div>}
    </div>
  );
}