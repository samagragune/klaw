/** Shared by server-rendered pages (landing, docs) and client components. */
export const REPO_URL = "https://github.com/samagragune/klaw";
const REPO_GIT = `${REPO_URL}.git`;

export const INSTALL_SUPERVISOR = `pip install "git+${REPO_GIT}#subdirectory=backend"`;
export const START_SUPERVISOR = "klaw-supervisor start";
export const INSTALL_COMPANION = `pip install "git+${REPO_GIT}#subdirectory=companion"`;
export const INSTALL_SDK = `pip install "git+${REPO_GIT}#subdirectory=sdk" openai-agents`;
export const INSTALL_SDK_ONLY = `pip install "git+${REPO_GIT}#subdirectory=sdk"`;
