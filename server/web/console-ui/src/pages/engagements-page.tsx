import { useParams } from "react-router-dom";
import { PageContent, PageHeader } from "@/components/page-header";
import { EngagementDetailPanel, EngagementList } from "@/ops/engagements";

/** Every engagement and chat on this machine, and — under ``/engagements/:id`` — what one holds. */
export function EngagementsPage() {
  const { id } = useParams();
  if (id) {
    return (
      <PageContent>
        <EngagementDetailPanel id={id} />
      </PageContent>
    );
  }
  return (
    <PageContent>
      <PageHeader
        title="Engagements"
        summary="Everything that has run on this machine: hunts and chats, their journal, their files, the exploits they catalogued."
      />
      <EngagementList />
    </PageContent>
  );
}
