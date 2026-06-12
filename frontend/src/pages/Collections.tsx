import { useEffect, useState } from "react";
import { Navigation } from "@/components/Navigation";
import { Footer } from "@/components/Footer";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/hooks/use-toast";
import {
  listCollections,
  createCollection,
  deleteCollection,
  listCollectionEditors,
  assignEditor,
  unassignEditor,
  type CollectionRecord,
  type CollectionEditor,
} from "@/services/collections";
import apiClient from "@/lib/api";

interface RegionOption {
  id: number;
  name: string;
  abbrev: string;
}

const Collections = () => {
  const { toast } = useToast();
  const [collections, setCollections] = useState<CollectionRecord[]>([]);
  const [regions, setRegions] = useState<RegionOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createDescription, setCreateDescription] = useState("");
  const [createRegionId, setCreateRegionId] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [editors, setEditors] = useState<CollectionEditor[]>([]);
  const [editorUserId, setEditorUserId] = useState<string>("");

  const reload = async () => {
    setLoading(true);
    try {
      const items = await listCollections();
      setCollections(items);
    } catch (err) {
      toast({
        title: "Failed to load Collections",
        description: err instanceof Error ? err.message : String(err),
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    reload();
    // Pull a flat region list for the create-collection dropdown.
    apiClient
      .get("/regions/", { params: { page_size: 500 } })
      .then((res) => {
        const data = res.data as { results?: unknown[] } | unknown[];
        const rows = Array.isArray(data) ? data : data.results || [];
        const opts: RegionOption[] = rows
          .map((raw) => {
            const r = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
            return {
              id: Number(r.id ?? r.region_id),
              name: String(r.name ?? "").trim(),
              abbrev: String(r.abbrev ?? "").trim(),
            };
          })
          .filter((r: RegionOption) => r.id && r.name);
        setRegions(opts.sort((a, b) => a.name.localeCompare(b.name)));
      })
      .catch(() => setRegions([]));
  }, []);

  const refreshEditors = async (collectionId: number) => {
    try {
      const items = await listCollectionEditors(collectionId);
      setEditors(items);
    } catch (err) {
      toast({
        title: "Failed to load editors",
        description: err instanceof Error ? err.message : String(err),
        variant: "destructive",
      });
    }
  };

  const onSelectCollection = async (id: number) => {
    setSelectedId(id);
    setEditorUserId("");
    await refreshEditors(id);
  };

  const onCreate = async () => {
    if (!createName.trim() || !createRegionId) {
      toast({ title: "Name and Region are required.", variant: "destructive" });
      return;
    }
    setSubmitting(true);
    try {
      await createCollection({
        name: createName.trim(),
        description: createDescription.trim(),
        region_id: createRegionId,
      });
      toast({ title: "Collection created." });
      setCreateOpen(false);
      setCreateName("");
      setCreateDescription("");
      setCreateRegionId(null);
      await reload();
    } catch (err) {
      toast({
        title: "Could not create Collection",
        description: err instanceof Error ? err.message : String(err),
        variant: "destructive",
      });
    } finally {
      setSubmitting(false);
    }
  };

  const onDelete = async (id: number, name: string) => {
    if (!confirm(`Remove Collection "${name}"? Contributions referencing it will block removal.`)) {
      return;
    }
    try {
      await deleteCollection(id);
      toast({ title: "Collection removed." });
      if (selectedId === id) setSelectedId(null);
      await reload();
    } catch (err) {
      toast({
        title: "Could not remove Collection",
        description: err instanceof Error ? err.message : String(err),
        variant: "destructive",
      });
    }
  };

  const onAssign = async () => {
    if (!selectedId) return;
    const uid = parseInt(editorUserId, 10);
    if (!uid || Number.isNaN(uid)) {
      toast({ title: "Enter a numeric user id.", variant: "destructive" });
      return;
    }
    try {
      await assignEditor(selectedId, uid);
      setEditorUserId("");
      await refreshEditors(selectedId);
      await reload();
      toast({ title: "Editor assigned." });
    } catch (err) {
      toast({
        title: "Could not assign editor",
        description: err instanceof Error ? err.message : String(err),
        variant: "destructive",
      });
    }
  };

  const onUnassign = async (userId: number) => {
    if (!selectedId) return;
    try {
      await unassignEditor(selectedId, userId);
      await refreshEditors(selectedId);
      await reload();
      toast({ title: "Editor unassigned." });
    } catch (err) {
      toast({
        title: "Could not unassign editor",
        description: err instanceof Error ? err.message : String(err),
        variant: "destructive",
      });
    }
  };

  const selected = collections.find((c) => c.id === selectedId) || null;

  return (
    <div className="min-h-screen flex flex-col">
      <Navigation />
      <div className="flex-1 bg-background">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          <div className="flex items-center justify-between mb-6">
            <div>
              <h1 className="text-2xl font-heading font-semibold">Collection Administration</h1>
              <p className="text-muted-foreground text-sm mt-1">
                Create institutional Collections (one per Region) and assign Editors. Editors gain
                review permissions automatically when assigned.
              </p>
            </div>
            <Button onClick={() => setCreateOpen((v) => !v)}>
              {createOpen ? "Cancel" : "New Collection"}
            </Button>
          </div>

          {createOpen && (
            <Card className="p-4 mb-6">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div>
                  <Label htmlFor="coll-name">Name</Label>
                  <Input
                    id="coll-name"
                    value={createName}
                    onChange={(e) => setCreateName(e.target.value)}
                  />
                </div>
                <div>
                  <Label htmlFor="coll-region">Region</Label>
                  <select
                    id="coll-region"
                    className="w-full h-10 rounded-md border border-input bg-background px-3 py-2 text-sm"
                    value={createRegionId ?? ""}
                    onChange={(e) =>
                      setCreateRegionId(e.target.value ? Number(e.target.value) : null)
                    }
                  >
                    <option value="">-- choose a Region --</option>
                    {regions.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.name} {r.abbrev ? `(${r.abbrev})` : ""}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <Label htmlFor="coll-desc">Description (optional)</Label>
                  <Input
                    id="coll-desc"
                    value={createDescription}
                    onChange={(e) => setCreateDescription(e.target.value)}
                  />
                </div>
              </div>
              <div className="flex justify-end gap-2 mt-4">
                <Button variant="ghost" onClick={() => setCreateOpen(false)} disabled={submitting}>
                  Cancel
                </Button>
                <Button onClick={onCreate} disabled={submitting}>
                  {submitting ? "Creating..." : "Create"}
                </Button>
              </div>
            </Card>
          )}

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <Card className="p-4 md:col-span-2">
              <h2 className="font-semibold mb-3">Collections</h2>
              {loading ? (
                <p className="text-sm text-muted-foreground">Loading…</p>
              ) : collections.length === 0 ? (
                <p className="text-sm text-muted-foreground">No Collections yet.</p>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left border-b">
                      <th className="py-2">Name</th>
                      <th className="py-2">Region</th>
                      <th className="py-2">Editors</th>
                      <th className="py-2">Active</th>
                      <th className="py-2"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {collections.map((c) => (
                      <tr
                        key={c.id}
                        className={`border-b hover:bg-muted/40 cursor-pointer ${
                          selectedId === c.id ? "bg-muted/60" : ""
                        }`}
                        onClick={() => onSelectCollection(c.id)}
                      >
                        <td className="py-2 font-medium">{c.name}</td>
                        <td className="py-2">
                          {c.region.name}
                          {c.region.abbrev ? (
                            <span className="text-muted-foreground"> ({c.region.abbrev})</span>
                          ) : null}
                        </td>
                        <td className="py-2">{c.editor_count}</td>
                        <td className="py-2">
                          {c.is_active ? (
                            <Badge variant="default">Active</Badge>
                          ) : (
                            <Badge variant="secondary">Inactive</Badge>
                          )}
                        </td>
                        <td className="py-2 text-right">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              onDelete(c.id, c.name);
                            }}
                          >
                            Delete
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>

            <Card className="p-4">
              <h2 className="font-semibold mb-3">Editors</h2>
              {!selected ? (
                <p className="text-sm text-muted-foreground">Select a Collection on the left.</p>
              ) : (
                <>
                  <p className="text-xs text-muted-foreground mb-3">
                    Editing assignments for <strong>{selected.name}</strong>.
                  </p>
                  <ul className="space-y-2 mb-4">
                    {editors.length === 0 ? (
                      <li className="text-sm text-muted-foreground">No editors assigned yet.</li>
                    ) : (
                      editors.map((e) => (
                        <li
                          key={e.id}
                          className="flex items-center justify-between text-sm border-b pb-1"
                        >
                          <span>
                            {e.username}
                            <span className="text-muted-foreground"> · #{e.user_id}</span>
                          </span>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => onUnassign(e.user_id)}
                          >
                            Remove
                          </Button>
                        </li>
                      ))
                    )}
                  </ul>
                  <Label htmlFor="assign-uid">Assign user (numeric ID)</Label>
                  <div className="flex gap-2 mt-1">
                    <Input
                      id="assign-uid"
                      value={editorUserId}
                      onChange={(e) => setEditorUserId(e.target.value)}
                      placeholder="e.g. 42"
                    />
                    <Button onClick={onAssign}>Assign</Button>
                  </div>
                  <p className="text-xs text-muted-foreground mt-2">
                    The user is automatically added to the Editors group, granting review
                    permissions for this Collection's contributions.
                  </p>
                </>
              )}
            </Card>
          </div>
        </div>
      </div>
      <Footer />
    </div>
  );
};

export default Collections;
