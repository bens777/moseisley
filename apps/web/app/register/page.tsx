import { Suspense } from "react";
import { AuthForm } from "@/components/auth-form";

export const metadata = { title: "Create your account" };

/* The account-creation page. Previously this route did not exist: registration
   was only reachable through a small toggle on /login, so anyone sent to
   /register landed on a 404. */
export default function RegisterPage() {
  return (
    <Suspense>
      <AuthForm initialMode="register" />
    </Suspense>
  );
}
