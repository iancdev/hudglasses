import "./globals.css";

export const metadata = {
  title: "iOS Audio Relay",
  description: "Relay iPhone mic audio to hudserver ESP32 endpoints",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

