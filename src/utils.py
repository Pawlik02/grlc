import static as static
import gquery as gquery
import pagination as pageUtils
import cgi
import traceback

import logging
from github import Github

from prov import grlcPROV
from fileLoaders import GithubLoader, LocalLoader
from sparql import SPARQL_FORMATS

glogger = logging.getLogger(__name__)


def getLoader(user, repo, sha=None, prov=None):
    if user is None and repo is None:
        loader = LocalLoader()
    else:
        loader = GithubLoader(user, repo, sha, prov)
    return loader

def build_spec(user, repo, sha=None, prov=None, extraMetadata=[]):
    '''
    Build grlc specification for the given github user / repo
    '''
    loader = getLoader(user, repo, sha, prov)

    files = loader.fetchFiles()
    raw_repo_uri = loader.getRawRepoUri()

    # Fetch all .rq files
    items = []

    for c in files:
        c_name = c['name']
        print '>>>>>>>>>>>>>>>>>>>>>>>>>c_name: ', c_name
        if ".rq" in c['name'] or ".tpf" in c['name'] or ".sparql" in c['name']:
            call_name = c['name'].split('.')[0]

            # Retrieve extra metadata from the query decorators
            query_text = loader.getTextFor(c)

            if ".rq" in c_name or ".sparql" in c_name:
                glogger.info("===================================================================")
                glogger.info("Processing SPARQL query: {}".format(c_name))
                glogger.info("===================================================================")
                item = process_sparql_query_text(query_text, raw_repo_uri, call_name, extraMetadata)
                if item: # TODO: On ERROR, no item gets generated. Perhaps we should propagate the error and notify the user
                    items.append(item)
            elif ".tpf" in c['name']:
                glogger.info("===================================================================")
                glogger.info("Processing TPF query: {}".format(c_name))
                glogger.info("===================================================================")
                item = process_tpf_query_text(query_text, raw_repo_uri, call_name, extraMetadata)
                if item:
                    items.append(item)
            else:
                glogger.info("Ignoring unsupported source call name: {}".format(c_name))

            projection = loader.getProjectionForQueryName(call_name)
            if projection:
                item['projection'] = projection

    return items

def process_tpf_query_text(query_text, raw_repo_uri, call_name, extraMetadata):
    query_metadata = gquery.get_yaml_decorators(query_text)

    tags = query_metadata['tags'] if 'tags' in query_metadata else []
    glogger.debug("Read query tags: " + ', '.join(tags))

    summary = query_metadata['summary'] if 'summary' in query_metadata else ""
    glogger.debug("Read query summary: " + summary)

    description = query_metadata['description'] if 'description' in query_metadata else ""
    glogger.debug("Read query description: " + description)

    method = query_metadata['method'].lower() if 'method' in query_metadata else "get"
    if method not in ['get', 'post', 'head', 'put', 'delete', 'options', 'connect']:
        method = "get"

    pagination = query_metadata['pagination'] if 'pagination' in query_metadata else ""
    glogger.debug("Read query pagination: " + str(pagination))

    endpoint = query_metadata['endpoint'] if 'endpoint' in query_metadata else ""
    glogger.debug("Read query endpoint: " + endpoint)

    # If this query allows pagination, add page number as parameter
    params = []
    if pagination:
        params.append(pageUtils.getSwaggerPaginationDef(pagination))

    item = getItemDefinition(call_name=call_name, method=method, tags=tags,
        summary=summary, description=description, params=params, query=query_metadata['query'])

    for extraField in extraMetadata:
        if extraField in query_metadata:
            item[extraField] = query_metadata[extraField]

    return item

def process_sparql_query_text(query_text, raw_repo_uri, call_name, extraMetadata):
    try:
        query_metadata = gquery.get_metadata(query_text)
    except Exception as e:
        raw_query_uri = raw_repo_uri + ' / ' + call_name
        glogger.error("Could not parse query at {}".format(raw_query_uri))
        glogger.error(traceback.print_exc())
        return None

    tags = query_metadata['tags'] if 'tags' in query_metadata else []
    glogger.debug("Read query tags: {}".format(', '.join(tags)))

    summary = query_metadata['summary'] if 'summary' in query_metadata else ""
    glogger.debug("Read query summary: {}".format(summary))

    description = query_metadata['description'] if 'description' in query_metadata else ""
    glogger.debug("Read query description: {}".format(description))

    method = query_metadata['method'].lower() if 'method' in query_metadata else ""
    if method not in ['get', 'post', 'head', 'put', 'delete', 'options', 'connect']:
        method = ""

    pagination = query_metadata['pagination'] if 'pagination' in query_metadata else ""
    glogger.debug("Read query pagination: {}".format(pagination))

    mime = query_metadata['mime'] if 'mime' in query_metadata else ""
    glogger.debug("Read endpoint dump MIME type: {}".format(mime))

    endpoint_in_url = query_metadata['endpoint_in_url'] if 'endpoint_in_url' in query_metadata else True
    glogger.debug("Read endpoint in url: {}".format(endpoint_in_url))

    endpoint = gquery.guess_endpoint_uri(query_text, raw_repo_uri)
    glogger.debug("Read query endpoint: {}".format(endpoint))

    if query_metadata['type'] == 'SelectQuery':
        try:
            parameters = gquery.get_parameters(query_text, endpoint)
        except Exception as e:
            glogger.error(e)
            glogger.error("Could not parse parameters of query {}".format(call_name))
            return None

        # TODO: do something intelligent with the parameters!
        # As per #3, prefetching IRIs via SPARQL and filling enum

        params = []
        for v, p in list(parameters.items()):
            params.append(getParameterDefinition(p))

    # If this query allows pagination, add page number as parameter
    if pagination:
        params.append(pageUtils.getSwaggerPaginationDef(pagination))

    if endpoint_in_url:
        endpoint_param = {}
        endpoint_param['name'] = "endpoint"
        endpoint_param['type'] = "string"
        endpoint_param['in'] = "query"
        endpoint_param['description'] = "Alternative endpoint for SPARQL query"
        params.append(endpoint_param)

    if query_metadata['type'] == 'SelectQuery':
        # We now know it is a SELECT query
        if not method:
            method = 'get'
        item_properties = {}
        for pv in query_metadata['variables']:
            item_properties[pv] = getItemPropertyDef(pv)
        item = getItemDefinition(call_name=call_name, method=method, tags=tags,
            summary=summary, description=description, params=params,
            item_properties=item_properties, query=query_metadata['query'])

    else:
        # We know it is an UPDATE; ignore params and props
        if not method:
            method = 'post'
        item = getItemDefinition(call_name=call_name, method=method, tags=tags,
            summary=summary, description=description, query=query_metadata['query'])

    for extraField in extraMetadata:
        if extraField in query_metadata:
            item[extraField] = query_metadata[extraField]

    return item

def getItemPropertyDef(pv):
    return {
        "name": pv,
        "type": "object",
        "required": ["type", "value"],
        "properties": {
            "type": {
                "type": "string"
            },
            "value": {
                "type": "string"
            },
            "xml:lang": {
                "type": "string"
            },
            "datatype": {
                "type": "string"
            }
        }
    }

def getItemDefinition(call_name, method, tags, summary, description, query, params=None, item_properties=None):
    item = {
        'call_name': call_name,
        'method': method,
        'tags': tags,
        'summary': summary,
        'description': description,
        'query': query
    }
    if params:
        item['params'] = params
    if params:
        item['item_properties'] = item_properties
    return item

def getParameterDefinition(p):
    param = {}
    param['name'] = p['name']
    param['type'] = p['type']
    param['required'] = p['required']
    param['in'] = "query"
    param['description'] = "A value of type {} that will substitute {} in the original query".format(p['type'], p['original'])
    if p['enum']:
        param['enum'] = p['enum']
    return param

def getInfoDefinition(version, repo_title, contact_name, contact_url, user_repo):
    return {
        'version': version,
        'title': repo_title,
        'contact': {
            'name': contact_name,
            'url': contact_url
        },
        'license': {
            'name' : 'License',
            'url': static.GITHUB_RAW_BASE_URL + user_repo + '/master/LICENSE'
        }
    }

def getSwaggerDescriptionDef(item):
    description = item['description'] + "\n<pre>\n{}\n</pre>".format(cgi.escape(item['query']))
    if 'projection' in item:
        description += "JSON results are projected according to: \n<pre>\n{}\n</pre>".format(cgi.escape(item['projection']))
    return description

def getPathsDefFromSpec(spec):
    swag_paths = {}
    for item in spec:
        swag_paths[item['call_name']] = {}
        swag_paths[item['call_name']][item['method']] = {
            "tags" : item['tags'],
            "summary" : item['summary'],
            "description" : getSwaggerDescriptionDef(item),
            "produces" : SPARQL_FORMATS.keys(), # ["text/csv", "application/json", "text/html"],
            "parameters": item['params'] if 'params' in item else None,
            "responses": {
                "200" : {
                    "description" : "Query response",
                    "schema" : {
                        "type" : "array",
                        "items": {
                            "type": "object",
                            "properties": item['item_properties'] if 'item_properties' in item else None
                        },
                    }
                },
                "default" : {
                    "description" : "Unexpected error",
                    "schema" : {
                        "$ref" : "#/definitions/Message"
                    }
                }
            }
        }
    return swag_paths

def build_swagger_spec(user, repo, sha, serverName):
    '''Build grlc specification for the given github user / repo in swagger format '''

    if user and repo:
        user_repo = user + '/' + repo
        api_repo_uri = static.GITHUB_API_BASE_URL + user_repo

        # Init provenance recording
        prov_g = grlcPROV(user, repo)
        gh = Github(static.ACCESS_TOKEN)
        gh_repo = gh.get_repo(user + '/' + repo)

        repo_title = gh_repo.name
        contact_name = gh_repo.owner.login
        contact_url = gh_repo.owner.html_url

        # Add the API URI as a used entity by the activity
        prov_g.add_used_entity(api_repo_uri)

        commit_list = [ c.sha for c in gh_repo.get_commits() ]

        prev_commit = None
        next_commit = None

        version = sha
        if sha is None:
            version = commit_list[0]

        if commit_list.index(version) < len(commit_list) - 1:
            prev_commit = commit_list[commit_list.index(version)+1]
        if commit_list.index(version) > 0:
            next_commit = commit_list[commit_list.index(version)-1]
    else:
        user_repo = 'local/local'
        prev_commit = []
        next_commit = []
        version = 'local'
        repo_title = 'local'
        contact_name = ''
        contact_url = ''
        prov_g = None

    spec = build_spec(user, repo, sha, prov_g)

    swag = {}
    swag['prev_commit'] = prev_commit
    swag['next_commit'] = next_commit
    swag['swagger'] = '2.0'
    swag['host'] = serverName
    swag['basePath'] = '/api/' + user_repo + ('/commit/' + sha + '/' if sha else '/')
    swag['schemes'] = ['http']
    swag['info'] = getInfoDefinition(version, repo_title, contact_name, contact_url, user_repo)
    swag['paths'] = getPathsDefFromSpec(spec)

    if prov_g:
        prov_g.end_prov_graph()
        swag['prov'] = prov_g.serialize(format='turtle')

    return swag
