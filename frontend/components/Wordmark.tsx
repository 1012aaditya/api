export function Wordmark({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
        <rect x="1.5" y="1.5" width="17" height="17" rx="4" fill="#2a78d6" />
        <path
          d="M6 6.5h4.2a3.5 3.5 0 0 1 0 7H6z"
          fill="none"
          stroke="#ffffff"
          strokeWidth="1.8"
          strokeLinejoin="round"
        />
      </svg>
      <span className="text-[15px] font-semibold tracking-tight text-ink">
        DocuParse
      </span>
    </span>
  );
}
