@description('The Azure region where resources will be deployed.')
param location string = resourceGroup().location

@description('Environment name (e.g. dev, staging, prod)')
param environmentName string = 'prod'

@description('Unique suffix for resource names')
param uniqueSuffix string = uniqueString(resourceGroup().id)

@description('App Service Plan SKU (e.g. B1, B2, S1, P1v3)')
param appServicePlanSku string = 'B1'

@description('Name of the MCP AI Portal Web App')
param mcpPortalAppName string = 'app-mcp-portal-${environmentName}-${uniqueSuffix}'

@description('Default LLM Provider')
param defaultLlmProvider string = 'groq'

@description('Default LLM Model Identifier')
param defaultLlmModel string = 'llama-3.3-70b-versatile'

@description('Default LLM API Endpoint')
param defaultLlmEndpoint string = 'https://api.groq.com/openai/v1/chat/completions'

// ==========================================
// 1. Log Analytics Workspace & App Insights
// ==========================================
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-mcp-portal-${environmentName}-${uniqueSuffix}'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-mcp-portal-${environmentName}-${uniqueSuffix}'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

// ==========================================
// 2. Linux App Service Plan
// ==========================================
resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: 'asp-mcp-portal-${environmentName}-${uniqueSuffix}'
  location: location
  kind: 'linux'
  sku: {
    name: appServicePlanSku
    tier: 'Basic'
  }
  properties: {
    reserved: true
  }
}

// ==========================================
// 3. MCP AI Portal Web App (Python 3.11 / 3.12)
// ==========================================
resource mcpPortalApp 'Microsoft.Web/sites@2023-12-01' = {
  name: mcpPortalAppName
  location: location
  kind: 'app,linux'
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      appCommandLine: 'bash startup.sh'
      alwaysOn: true
      http20Enabled: true
      appSettings: [
        {
          name: 'WEBSITES_PORT'
          value: '5000'
        }
        {
          name: 'PORT'
          value: '5000'
        }
        {
          name: 'FLASK_PORT'
          value: '5000'
        }
        {
          name: 'FLASK_HOST'
          value: '0.0.0.0'
        }
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'true'
        }
        {
          name: 'ENABLE_ORYX_BUILD'
          value: 'true'
        }
        {
          name: 'WEBSITES_CONTAINER_START_TIME_LIMIT'
          value: '600'
        }
        {
          name: 'LLM_PROVIDER'
          value: defaultLlmProvider
        }
        {
          name: 'LLM_MODEL'
          value: defaultLlmModel
        }
        {
          name: 'LLM_ENDPOINT'
          value: defaultLlmEndpoint
        }
        {
          name: 'APPINSIGHTS_INSTRUMENTATIONKEY'
          value: appInsights.properties.InstrumentationKey
        }
      ]
    }
  }
}

// ==========================================
// Outputs
// ==========================================
output mcpPortalAppName string = mcpPortalApp.name
output mcpPortalAppUrl string = 'https://${mcpPortalApp.properties.defaultHostName}'
output appServicePlanName string = appServicePlan.name
