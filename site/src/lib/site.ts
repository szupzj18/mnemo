export const REPO = "https://github.com/szupzj18/mnemo"
export const DOCS = `${REPO}/tree/main/docs`
export const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? ""
export const asset = (p: string) => `${BASE}${p}`

export const INSTALL = "git clone https://github.com/szupzj18/mnemo ~/mnemo && ~/mnemo/bin/mnemo index"
