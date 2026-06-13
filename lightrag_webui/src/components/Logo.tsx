// Логотип ПростоГраф — мини-граф (узлы + рёбра). Цвет наследуется от
// currentColor (используйте text-primary). Маленький и аккуратный.
export default function Logo({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      aria-hidden="true"
      role="img"
    >
      <g stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" opacity="0.75">
        <line x1="6" y1="7.5" x2="17" y2="5" />
        <line x1="6" y1="7.5" x2="8" y2="17" />
        <line x1="17" y1="5" x2="18.5" y2="15.5" />
        <line x1="8" y1="17" x2="18.5" y2="15.5" />
        <line x1="8" y1="17" x2="17" y2="5" />
      </g>
      <g fill="currentColor">
        <circle cx="6" cy="7.5" r="2.4" />
        <circle cx="17" cy="5" r="1.9" />
        <circle cx="8" cy="17" r="1.9" />
        <circle cx="18.5" cy="15.5" r="2.4" />
      </g>
    </svg>
  )
}
