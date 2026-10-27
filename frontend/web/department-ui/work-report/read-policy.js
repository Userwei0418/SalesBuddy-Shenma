// Aggregate report reads can outlast ordinary reads; writes and other modules
// keep their existing shared-client policy.
export const REPORT_READ_TIMEOUT = 30000;
export const REPORT_SLOW_READ_AFTER = 10000;
