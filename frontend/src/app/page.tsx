"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Spinner } from "@/components/ui";
import { homeFor, useMe } from "@/lib/session";

export default function Home() {
  const me = useMe();
  const router = useRouter();
  useEffect(() => {
    if (me.data) router.replace(homeFor(me.data));
    else if (me.isError) router.replace("/login");
  }, [me.data, me.isError, router]);
  return <Spinner />;
}
