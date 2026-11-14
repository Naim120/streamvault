/**
 * Security & UX Error Sanitizer
 * Prevents internal backend, database, and infrastructure leakage to end users.
 */
export function sanitizeAuthError(error: any): string {
  if (!error) return 'An unexpected error occurred. Please try again.';
  const raw = typeof error === 'string' ? error : error.message || '';
  const lower = raw.toLowerCase();

  if (
    lower.includes('invalid login credentials') ||
    lower.includes('invalid credentials') ||
    lower.includes('database error querying schema') ||
    lower.includes('invalid password')
  ) {
    return 'Invalid email or password. Please verify your credentials.';
  }

  if (lower.includes('user already registered') || lower.includes('already exists')) {
    return 'An account with this email already exists. Try signing in.';
  }

  if (
    lower.includes('email not confirmed') ||
    lower.includes('auth session missing') ||
    lower.includes('session missing')
  ) {
    return 'Your email is not verified yet. Please check your inbox and click the verification link sent to your email, then click here again.';
  }

  if (lower.includes('password') && (lower.includes('least') || lower.includes('short') || lower.includes('character'))) {
    return 'Password must be at least 6 characters long.';
  }

  if (lower.includes('email') && lower.includes('invalid')) {
    return 'Please enter a valid email address.';
  }

  if (lower.includes('rate limit') || lower.includes('too many requests')) {
    return 'Too many attempts. Please wait a moment before trying again.';
  }

  if (lower.includes('network') || lower.includes('failed to fetch') || lower.includes('timeout')) {
    return 'Network connection issue. Please check your connection and retry.';
  }

  // Catch any internal DB / schema leaks
  if (
    lower.includes('postgres') ||
    lower.includes('supabase') ||
    lower.includes('schema') ||
    lower.includes('table') ||
    lower.includes('relation') ||
    lower.includes('column') ||
    lower.includes('foreign key') ||
    lower.includes('sql')
  ) {
    return 'Unable to process your request at this time. Please try again later.';
  }

  return raw.length > 70 ? 'An unexpected error occurred. Please try again.' : raw;
}
