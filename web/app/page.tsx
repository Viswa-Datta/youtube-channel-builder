import { redirect } from "next/navigation";
import { supabaseServer } from "@/lib/supabase/server";

export default async function Home() {
  const sb = await supabaseServer();
  const { data } = await sb.auth.getUser();
  redirect(data.user ? "/dashboard" : "/login");
}
