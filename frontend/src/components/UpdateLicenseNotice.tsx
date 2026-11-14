/** Shown before updating and after restart; links refer to the offered release, not the installed version. */
export default function UpdateLicenseNotice({ version }: { version: string }) {
  const repository = "https://github.com/Abe-Borg/spec-surgeon";
  const tag = version ? `v${encodeURIComponent(version)}` : "";
  const license = `${repository}/blob/${tag || "master"}/LICENSE`;
  const source = tag ? `${repository}/releases/tag/${tag}` : `${repository}/releases`;
  const linkClass = "text-accent underline underline-offset-2 hover:text-accent-hover";
  return (
    <div className="mt-2 space-y-1 text-xs leading-relaxed text-ink-dim" aria-label="Update license information">
      <p>
        <strong className="font-semibold text-ink">License: GNU AGPL v3 only (AGPL-3.0-only).</strong>{" "}
        Free to use, including commercially. Sharing copies or modified versions requires complying with the AGPL,
        including providing Corresponding Source.
      </p>
      <p>
        Provided as is, without warranty; liability is excluded to the extent permitted by applicable law.
        Updates skip the installer’s license page. Review the{" "}
        <a href={license} target="_blank" rel="noreferrer noopener" className={linkClass} data-capability="update.license">
          full license for {version ? `version ${version}` : "the app"}
        </a>{" "}
        and its{" "}
        <a href={source} target="_blank" rel="noreferrer noopener" className={linkClass} data-capability="update.license">
          complete source
        </a>.
        {" "}Installed terms are always available in About → Licenses.
      </p>
    </div>
  );
}
