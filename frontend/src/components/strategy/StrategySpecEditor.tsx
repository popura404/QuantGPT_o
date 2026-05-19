interface Props {
  value: string;
  onChange: (value: string) => void;
}

export default function StrategySpecEditor({ value, onChange }: Props) {
  return (
    <section className="rounded-lg border border-gray-200 bg-white p-3">
      <div className="mb-2 text-sm font-semibold text-gray-900">Raw JSON</div>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        spellCheck={false}
        className="min-h-[620px] w-full rounded-md border border-gray-100 bg-gray-50 p-4 font-mono text-xs leading-5 text-gray-800 outline-none focus:border-blue-500"
      />
    </section>
  );
}
