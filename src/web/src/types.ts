export type RegistrationRole = "seeker" | "recruiter";
export type JsonMap = Record<string, any>;

export interface Account extends JsonMap {
  id: string;
  email: string;
  registration_role: RegistrationRole;
  admin_permissions: string[];
}

export interface SessionState {
  token: string;
  csrf: string;
  account: Account | null;
}
