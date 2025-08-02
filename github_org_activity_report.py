#!/usr/bin/env python3
"""
GitHub Org-Wide Activity Report – v4
- Month-by-month per-user CSV
- Org-wide “wide” CSV (one row per user)
- Supports --org, --user, --since, --until
- Tracks: PR size, approvals/comments given, comments received per PR, merge time, top repos, most active hours
"""

import os, sys, argparse, requests
from datetime import datetime
from collections import defaultdict, Counter
from dateutil import parser as dtp
import pandas as pd
import time
import json

TOKEN = os.getenv("GH_TOKEN")
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
GQL_URL = "https://api.github.com/graphql"

def run_gql(query, variables=None, max_retries=5):
    tries = 0
    while tries < max_retries:
        try:
            resp = requests.post(
                GQL_URL, json={"query": query, "variables": variables or {}},
                headers=HEADERS, timeout=30
            )
            # Print every error status
            if resp.status_code != 200:
                print(f"[GQL] HTTP {resp.status_code}: {resp.reason}")
                print(f"  URL: {resp.url}")
                print(f"  Variables: {json.dumps(variables, indent=2)}")
                print("  Headers:")
                for k in ['X-RateLimit-Remaining', 'X-RateLimit-Reset', 'Retry-After']:
                    if k in resp.headers:
                        print(f"    {k}: {resp.headers[k]}")
                print("  Body:", resp.text[:1000])  # Show up to 1000 chars
            # Retry on 403 + rate limit in body
            if resp.status_code == 403 and ("rate limit" in resp.text.lower() or "secondary rate limit" in resp.text.lower()):
                wait_sec = 60 if tries == 0 else 120
                print(f"⚠️ Rate limit hit, sleeping {wait_sec}s... (Try {tries+1}/{max_retries})")
                time.sleep(wait_sec)
                tries += 1
                continue
            resp.raise_for_status()
            j = resp.json()
            if "errors" in j:
                print("[GQL] GraphQL errors:")
                for err in j["errors"]:
                    print(f"  Path: {err.get('path')}, Message: {err.get('message')}")
                print("  Variables:", json.dumps(variables, indent=2))
                raise RuntimeError(j["errors"])
            return j["data"]
        except Exception as e:
            print(f"❌ Exception in run_gql (try {tries+1}/{max_retries}): {e}")
            time.sleep(5)
            tries += 1
    raise RuntimeError("Hit GitHub API rate limit or error too many times.")

def paginate(query, variables, path):
    cursor = None
    while True:
        v = variables | {"after": cursor}
        data = run_gql(query, v)
        node = data
        for p in path:
            node = node[p]
        for item in node["nodes"]:
            yield item
        if not node["pageInfo"]["hasNextPage"]:
            break
        cursor = node["pageInfo"]["endCursor"]

def get_org_members(org):
    q = """
    query($org:String!, $after:String){
      organization(login:$org){
        membersWithRole(first:100, after:$after){
          pageInfo{hasNextPage endCursor}
          nodes{ login }
        }
      }
    }"""
    return [m["login"] for m in paginate(q, {"org": org}, ["organization", "membersWithRole"])]

def get_top_repos(org, limit=30):
    q = """
    query($org:String!){
      organization(login:$org){
        repositories(first:100, orderBy:{field:PUSHED_AT, direction:DESC}){
          nodes{ name nameWithOwner updatedAt }
        }
      }
    }"""
    data = run_gql(q, {"org": org})
    return [r["name"] for r in data["organization"]["repositories"]["nodes"][:limit]]

def in_window(ts, since, until):
    dt = dtp.parse(ts).replace(tzinfo=None)
    if since and dt < since:
        return False
    if until and dt > until:
        return False
    return True

def collect_user_data(org, user, repos, since=None, until=None):
    pr_metrics = []
    review_given_by_month = Counter()
    comments_given_by_month = Counter()
    comments_received_by_month = defaultdict(list)
    repo_counter_by_month = defaultdict(Counter)
    hour_counter_by_month = defaultdict(Counter)
    conversations_opened_by_month = Counter()

    window_filter = ""
    if since and until:
        window_filter = f" created:{since.strftime('%Y-%m-%d')}..{until.strftime('%Y-%m-%d')}"
    elif since:
        window_filter = f" created:>={since.strftime('%Y-%m-%d')}"
    elif until:
        window_filter = f" created:<={until.strftime('%Y-%m-%d')}"
    search_str = f"org:{org} author:{user} is:pr{window_filter}"
    # Collect PRs authored by the user
    pr_query = """
    query($query:String!, $after:String){
      search(query:$query, type:ISSUE, first:100, after:$after){
        pageInfo { hasNextPage endCursor }
        nodes {
          ... on PullRequest {
            number
            repository { name }
            title state createdAt mergedAt additions deletions
            comments(first:100){ nodes{ author{login} createdAt } }
            reviews(first:100){ nodes{ author{login} createdAt body } }
          }
        }
      }
    }"""
    #search_str = f"org:{org} author:{user} is:pr"
    for pr in paginate(pr_query, {"query": search_str}, ["search"]):
        repo = pr["repository"]["name"]
        created = dtp.parse(pr["createdAt"]).replace(tzinfo=None)
        merged = dtp.parse(pr["mergedAt"]).replace(tzinfo=None) if pr["mergedAt"] else None
        additions = pr["additions"]
        deletions = pr["deletions"]
        month = created.strftime("%Y-%m")
        if (since and created < since) or (until and created > until):
            continue
        # Collect comments (received) on their PR
        num_comments = 0
        if pr["comments"]:
            for c in pr["comments"]["nodes"]:
                if c["author"] and c["author"]["login"] != user:
                    num_comments += 1
        # Also add review comments from others
        first_review_time = None
        for r in pr["reviews"]["nodes"]:
            if r["author"] and r["author"]["login"] != user:
                num_comments += 1
                rt = dtp.parse(r["createdAt"]).replace(tzinfo=None)
                if (not first_review_time) or (rt < first_review_time):
                    first_review_time = rt
        comments_received_by_month[month].append(num_comments)
        # Time from first review to merge
        time_to_merge = (merged - first_review_time).total_seconds()/3600 if (merged and first_review_time) else None
        pr_metrics.append({
            "repo": repo, "created": created, "merged": merged,
            "additions": additions, "deletions": deletions,
            "time_to_merge_h": time_to_merge,
            "month": month,
        })
        repo_counter_by_month[month][repo] += 1
        hour_counter_by_month[month][created.hour] += 1

    # Collect approvals and comments given (on PRs not by self)
    approvals_seen = set()
    conversations_seen = set()
    for repo in repos:
        window_filter = ""
        if since and until:
            window_filter = f" created:{since.strftime('%Y-%m-%d')}..{until.strftime('%Y-%m-%d')}"
        elif since:
            window_filter = f" created:>={since.strftime('%Y-%m-%d')}"
        elif until:
            window_filter = f" created:<={until.strftime('%Y-%m-%d')}"
        # This will scope to PRs in the repo and date window
        search_str = f"org:{org} repo:{org}/{repo} is:pr{window_filter}"
        review_search_query = """
        query($query:String!, $after:String){
          search(query:$query, type:ISSUE, first:100, after:$after){
            pageInfo { hasNextPage endCursor }
            nodes {
              ... on PullRequest {
                number
                repository { name }
                author { login }
                createdAt
                comments(first:50){ nodes{ author{login} createdAt } }
                reviews(first:50){ nodes{ author{login} createdAt state } }
              }
            }
          }
        }
        """
        for pr in paginate(review_search_query, {"query": search_str}, ["search"]):
            pr_num = pr.get("number")
            repo_name = pr["repository"]["name"] if "repository" in pr and pr["repository"] and "name" in pr["repository"] else None
            if not repo_name:
              print(f"Skipping PR with missing repo: PR num {pr_num}, pr={pr}")
            if pr_num is not None:
              pr_num = int(pr_num)
            pr_author = pr["author"]["login"] if pr["author"] else None
            # Comments given (only on others' PRs)
            for c in pr["comments"]["nodes"]:
                if c["author"] and c["author"]["login"] == user and pr_author != user:
                    ts = dtp.parse(c["createdAt"]).replace(tzinfo=None)
                    if in_window(c["createdAt"], since, until):
                        month = ts.strftime("%Y-%m")
                        comments_given_by_month[month] += 1
                        hour_counter_by_month[month][ts.hour] += 1
            # Approvals given (on others' PRs)
            approval_key = (repo_name, pr_num)
            was_approver = False
            for r in pr["reviews"]["nodes"]:
                if r["author"] and r["author"]["login"] == user and pr_author != user and r["state"] == "APPROVED":
                    ts = dtp.parse(r["createdAt"]).replace(tzinfo=None)
                    if in_window(r["createdAt"], since, until):
                        month = ts.strftime("%Y-%m")
                        if approval_key not in approvals_seen:
                          review_given_by_month[month] += 1
                          approvals_seen.add(approval_key)
                          #print(f"[APPROVAL] User '{user}' approved PR #{pr_num} in repo '{repo_name}' (month {month})")
                          hour_counter_by_month[month][ts.hour] += 1
                        was_approver = True    
            # Conversations opened (review threads started by user on others' PRs)
            # For each PR not authored by the user, fetch threads
            # Conversations opened (review threads started by user on others' PRs)
            if pr_author != user and pr_num:
                thread_query = """
                query($org:String!, $repo:String!, $prNum:Int!){
                  repository(owner:$org, name:$repo){
                    pullRequest(number:$prNum){
                      reviewThreads(first:50){
                        nodes{
                          comments(first:1){
                            nodes{
                              id
                              author { login }
                              createdAt
                            }
                          }
                        }
                      }
                    }
                  }
                }"""
                if pr_author != user and pr_num and repo_name and was_approver:
                  pr_data = run_gql(thread_query, {"org": org, "repo": repo_name, "prNum": int(pr_num)})
                  threads = pr_data["repository"]["pullRequest"]["reviewThreads"]["nodes"]
                  for th in threads:
                      if th["comments"]["nodes"]:
                          first_comment = th["comments"]["nodes"][0]
                          comment_id = first_comment["id"]
                          conversations_key = (comment_id)
                          #print(f"  Thread: {first_comment['author']['login']} {first_comment['createdAt']} on PR {repo}#{pr_num}")
                          if (first_comment["author"] and
                              first_comment["author"]["login"].lower() == user.lower()):
                              ts = dtp.parse(first_comment["createdAt"]).replace(tzinfo=None)
                              if in_window(first_comment["createdAt"], since, until):
                                  month = ts.strftime("%Y-%m")
                                  if conversations_key not in conversations_seen:
                                      conversations_opened_by_month[month] += 1
                                      conversations_seen.add(conversations_key)



    # Per-month summary DataFrame
    df = pd.DataFrame(pr_metrics)
    results = []
    months = sorted(set(df["month"]).union(comments_given_by_month.keys(), review_given_by_month.keys()))
    for month in months:
        dff = df[df["month"] == month]
        pr_opened = len(dff)
        pr_merged = dff["merged"].notna().sum() if not dff.empty else 0
        avg_add = dff["additions"].mean() if not dff.empty else 0
        avg_del = dff["deletions"].mean() if not dff.empty else 0
        avg_merge_time = dff["time_to_merge_h"].mean() if not dff.empty else 0
        approvals_given = review_given_by_month[month]
        comments_given = comments_given_by_month[month]
        comments_rec = comments_received_by_month[month]
        convs_opened = conversations_opened_by_month[month]
        avg_comments_rec_per_pr = round(sum(comments_rec) / len(comments_rec), 2) if comments_rec else 0
        top_repos = ",".join([r for r, _ in repo_counter_by_month[month].most_common(3)])
        active_hours = ",".join([str(h) for h, _ in hour_counter_by_month[month].most_common(3)])
        results.append({
            "user": user,
            "month": month,
            "pr_opened": pr_opened,
            "pr_merged": pr_merged,
            "avg_additions": round(avg_add, 1),
            "avg_deletions": round(avg_del, 1),
            "avg_merge_time_h": round(avg_merge_time, 2),
            "approvals_given": approvals_given,
            "comments_given": comments_given,
            "conversations_opened_when_approver": convs_opened,
            "avg_comments_received_per_pr": avg_comments_rec_per_pr,
            "top_repos": top_repos,
            "most_active_hours": active_hours
        })
    return pd.DataFrame(results)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", required=True, help="GitHub org slug")
    ap.add_argument("--user", default=None, help="Optional: only run for one user")
    ap.add_argument("--since", default=None, help="YYYY-MM-DD")
    ap.add_argument("--until", default=None, help="YYYY-MM-DD")
    args = ap.parse_args()

    if not TOKEN:
        sys.exit("Set GH_TOKEN env var first (export GH_TOKEN=...)")
    since = dtp.parse(args.since).replace(tzinfo=None) if args.since else None
    until = dtp.parse(args.until).replace(tzinfo=None) if args.until else None

    members = [args.user] if args.user else get_org_members(args.org)
    repos = get_top_repos(args.org)
    all_results = []
    for user in members:
        print(f"Processing {user}...")
        df = collect_user_data(args.org, user, repos, since, until)
        if not df.empty:
            # Per-user month-by-month
            df.to_csv(f"{user}_{args.org}_summary.csv", index=False)
            # For org-wide: sum/avg over all months in window
            rec = dict(user=user)
            cols = ["pr_opened", "pr_merged", "avg_additions", "avg_deletions",
                    "avg_merge_time_h", "approvals_given", "comments_given", "conversations_opened_when_approver", "avg_comments_received_per_pr",
                    "top_repos", "most_active_hours"]
            for c in cols:
                if c.startswith("avg_"):
                    rec[c] = round(df[c].mean(), 2)
                elif c in ["top_repos", "most_active_hours"]:
                    # collapse all months' top repos/hours and count
                    items = Counter()
                    for v in df[c]:
                        if v:
                            for i in v.split(","):
                                if i: items[i] += 1
                    rec[c] = ",".join([i for i, _ in items.most_common(3)])
                else:
                    rec[c] = int(df[c].sum())
            all_results.append(rec)
    if all_results:
        pd.DataFrame(all_results).to_csv(f"{args.org}_orgwide_report.csv", index=False)
        print(f"\n✅ Org-wide report generated: {args.org}_orgwide_report.csv")
    else:
        print("No data found for selected users.")

if __name__ == "__main__":
    main()
