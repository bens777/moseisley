import { Suspense } from "react";
import { AuthForm } from "@/components/auth-form";

export const metadata = { title: "Log in" };

export default function LoginPage() {
  return (
    <Suspense>
      <AuthForm initialMode="login" />
    </Suspense>
  );
}
