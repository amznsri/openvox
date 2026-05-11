import { z } from "zod";

const envSchema = z.object({
  NODE_ENV: z.enum(["development", "production", "test"]).default("development"),
  PORT: z.coerce.number().default(3001),
  LOG_LEVEL: z.string().default("info"),
  CORE_API_URL: z.string().default("http://localhost:8000"),
  JWT_SECRET: z.string().default("change-me"),
  OPENVOX_AUTH: z.enum(["disabled", "enabled"]).default("disabled"),
  GITHUB_OAUTH_CLIENT_ID: z.string().default(""),
  GITHUB_OAUTH_CLIENT_SECRET: z.string().default(""),
  GOOGLE_OAUTH_CLIENT_ID: z.string().default(""),
  GOOGLE_OAUTH_CLIENT_SECRET: z.string().default(""),
  TWILIO_ACCOUNT_SID: z.string().default(""),
  TWILIO_AUTH_TOKEN: z.string().default(""),
  WHATSAPP_VERIFY_TOKEN: z.string().default(""),
  TELEGRAM_BOT_TOKEN: z.string().default(""),
});

export const env = envSchema.parse(process.env);
export type Env = z.infer<typeof envSchema>;
