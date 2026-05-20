import { useState } from "react";
import type { ReactNode } from "react";
import { createReportUrl, getReportUrl } from "../api/client";

interface Props {
  reportUrl: string;
  className?: string;
  children: ReactNode;
  download?: boolean;
  title?: string;
}

export default function ReportLink({ reportUrl, className, children, download = false, title }: Props) {
  const [loading, setLoading] = useState(false);

  async function handleClick(event: React.MouseEvent<HTMLAnchorElement>) {
    event.preventDefault();
    if (loading) return;
    setLoading(true);
    try {
      const url = await createReportUrl(reportUrl);
      if (download) {
        const link = document.createElement("a");
        link.href = url;
        link.download = "";
        link.rel = "noopener noreferrer";
        document.body.appendChild(link);
        link.click();
        link.remove();
      } else {
        window.open(url, "_blank", "noopener,noreferrer");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <a
      href={getReportUrl(reportUrl)}
      onClick={handleClick}
      target="_blank"
      rel="noopener noreferrer"
      className={className}
      aria-busy={loading}
      title={title}
    >
      {children}
    </a>
  );
}
