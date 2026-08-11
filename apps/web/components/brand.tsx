export function Wordmark({ size = "text-lg" }: { size?: string }) {
  return (
    <span className={`font-display font-bold tracking-tight ${size}`}>
      <span aria-hidden className="mr-1.5 select-none font-mono text-brand">▸</span>
      moseisley<span className="text-brand">.sh</span>
    </span>
  );
}
