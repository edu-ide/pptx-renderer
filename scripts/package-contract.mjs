/**
 * Build a cross-platform npm pack invocation whose stdout stays machine-readable.
 *
 * The caller packs an isolated manifest with lifecycle scripts removed. The npm flags add a
 * second guard and keep `--json` stdout machine-readable. Windows command shims must run
 * through cmd.exe because Node cannot exec `.cmd` files directly.
 */
export function getNpmPackInvocation({
  cachePath,
  comSpec = process.env.ComSpec ?? process.env.COMSPEC,
  platform = process.platform,
}) {
  const arguments_ = [
    'pack',
    '--dry-run',
    '--json',
    '--ignore-scripts',
    '--foreground-scripts=false',
    '--cache',
    cachePath,
  ];

  if (platform === 'win32') {
    return {
      executable: comSpec || 'cmd.exe',
      arguments: ['/d', '/c', 'npm.cmd', ...arguments_],
    };
  }

  return { executable: 'npm', arguments: arguments_ };
}

export function getPackageAuditManifest(packageJson) {
  const { scripts: _lifecycleScripts, ...auditManifest } = packageJson;
  return auditManifest;
}
