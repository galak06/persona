import type { BrandCreateRequest } from "../api/brands";

/**
 * Field config + validation for `BrandForm.tsx` — split out to keep that
 * file under the project's 300-line limit. Pure data/helpers, no JSX.
 */

// All fields kept as strings while editing; the 4 list fields are split on
// submit. Keys match `BrandCreateRequest` so building the payload is a
// straight map, not a hand-maintained duplicate list.
export type FormState = Record<keyof BrandCreateRequest, string>;

export const EMPTY_FORM: FormState = {
  name: "",
  site_url: "",
  niche: "",
  target_audience: "",
  mascot_name: "",
  brand_persona: "",
  instagram_profile_url: "",
  facebook_page_url: "",
  primary_keywords: "",
  secondary_keywords: "",
  competitor_mentions: "",
  competitor_accounts: "",
};

export interface TextFieldSpec {
  key: keyof FormState;
  label: string;
  required?: boolean;
  placeholder?: string;
}

export interface FieldSection {
  title: string;
  description: string;
  fields: TextFieldSpec[];
}

export const FIELD_SECTIONS: FieldSection[] = [
  {
    title: "Identity",
    description: "The basics — used across every generated config file and post.",
    fields: [
      { key: "name", label: "Name", required: true, placeholder: "Dog Food and Fun" },
      { key: "site_url", label: "Site URL", required: true, placeholder: "https://example.com" },
      { key: "niche", label: "Niche", required: true, placeholder: "Dog food & gear reviews" },
      { key: "target_audience", label: "Target audience" },
      { key: "mascot_name", label: "Mascot name" },
    ],
  },
  {
    title: "Social profiles",
    description: "Where the scanners look for your existing presence.",
    fields: [
      { key: "instagram_profile_url", label: "Instagram profile URL" },
      { key: "facebook_page_url", label: "Facebook page URL" },
    ],
  },
];

export const LIST_FIELDS: { key: keyof FormState; label: string }[] = [
  { key: "primary_keywords", label: "Primary keywords" },
  { key: "secondary_keywords", label: "Secondary keywords" },
  { key: "competitor_mentions", label: "Competitor mentions" },
  { key: "competitor_accounts", label: "Competitor accounts" },
];

// Only site_url/instagram_profile_url/facebook_page_url are validated —
// the only fields that break a running scanner if malformed.
export const URL_FIELD_KEYS: (keyof FormState)[] = [
  "site_url",
  "instagram_profile_url",
  "facebook_page_url",
];

export function parseList(value: string): string[] {
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

export function isValidUrl(value: string): boolean {
  if (!value.trim()) return true; // blank is valid here; required-ness is checked separately
  try {
    const parsed = new URL(value.trim());
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}
