// Client-side form checks. They mirror the server's rules (crates/server/src/auth/rules.rs,
// characters.rs) so players see mistakes before submitting; the server stays the authority.

export const PASSWORD_MIN = 10;
export const PASSWORD_MAX = 128;
export const NAME_MIN = 3;
export const NAME_MAX = 24;

/** A problem with the password, or null if it looks acceptable. */
export function passwordProblem(password: string, email: string): string | null {
  const len = [...password].length; // characters, as the server counts them
  if (len < PASSWORD_MIN) return `Use at least ${PASSWORD_MIN} characters.`;
  if (len > PASSWORD_MAX) return `Use at most ${PASSWORD_MAX} characters.`;
  if (password.trim().toLowerCase() === email.trim().toLowerCase()) return "Your password can't be your email address.";
  return null;
}

/**
 * A problem with a character name, or null. Names are 3 to 24 Latin letters
 * with single spaces, hyphens or apostrophes between them ("Mary-Kate O'Neil").
 */
export function nameProblem(name: string): string | null {
  const n = name.trim();
  const len = [...n].length;
  if (len < NAME_MIN || len > NAME_MAX) return `Names are ${NAME_MIN} to ${NAME_MAX} letters long.`;
  if (!/^\p{Script=Latin}+(?:[ '-]\p{Script=Latin}+)*$/u.test(n)) {
    return "Use letters only, with single spaces, hyphens or apostrophes between them.";
  }
  return null;
}
