/**
 * Display metadata per external service.
 *
 * The UI never hard-codes a provider anywhere else: a card reads the `service`
 * the backend recorded and looks it up here. Adding Railway, Telegram or
 * Instagram later is one entry in this table — no component changes.
 */

export interface ServiceMeta {
  label: string;
  glyph: string;
}

const SERVICES: Record<string, ServiceMeta> = {
  github: { label: "GitHub", glyph: "◇" },
  railway: { label: "Railway", glyph: "▲" },
  telegram: { label: "Telegram", glyph: "✈" },
  instagram: { label: "Instagram", glyph: "◎" },
  erp: { label: "ERP", glyph: "▦" },
};

const LOCAL: ServiceMeta = { label: "Local", glyph: "▪" };

export function serviceMeta(service: string | null | undefined): ServiceMeta {
  if (!service) return LOCAL;
  return (
    SERVICES[service] ?? {
      // An unknown provider still renders sensibly rather than breaking.
      label: service.charAt(0).toUpperCase() + service.slice(1),
      glyph: "◆",
    }
  );
}

export function knownServices(): string[] {
  return Object.keys(SERVICES);
}
