export const REPO = "https://github.com/szupzj18/mnemo"
export const DOCS = `${REPO}/tree/main/docs`
export const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? ""
export const asset = (p: string) => `${BASE}${p}`

export const INSTALL = "curl -fsSL https://szupzj18.github.io/mnemo/install.sh | sh"
export const UV_INSTALL = "uv tool install mnemo-search && mnemo setup"
