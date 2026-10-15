import { NextRequest, NextResponse } from "next/server";

const CANONICAL_HOST = "holodorigacha.com";
const WWW_HOST = "www.holodorigacha.com";

export function middleware(request: NextRequest) {
  const forwardedHost = request.headers.get("x-forwarded-host");
  const host = (
    forwardedHost
    || request.headers.get("host")
    || ""
  )
    .split(",")[0]
    .trim()
    .split(":")[0]
    .toLowerCase();

  if (host !== WWW_HOST) {
    return NextResponse.next();
  }

  const url = request.nextUrl.clone();
  url.protocol = "https:";
  url.host = CANONICAL_HOST;

  return NextResponse.redirect(url, 308);
}

export const config = {
  matcher: "/:path*",
};
