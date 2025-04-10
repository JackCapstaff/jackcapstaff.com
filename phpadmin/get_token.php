<?php

require 'vendor/autoload.php'; // Ensure Guzzle is loaded

use GuzzleHttp\Client;

function getAccessToken($tenantId, $clientId, $clientSecret) {
    // Create a client to send the request
    $client = new Client();

    // Prepare the request to get the access token
    try {
        $response = $client->post("https://login.microsoftonline.com/$tenantId/oauth2/v2.0/token", [
            'form_params' => [
                'client_id' => $clientId,
                'scope' => 'https://graph.microsoft.com/.default', // Using Microsoft Graph API
                'client_secret' => $clientSecret,
                'grant_type' => 'client_credentials',
            ],
        ]);

        // Decode the response
        $body = json_decode((string)$response->getBody(), true);

        // Check if we got an access token
        if (isset($body['access_token'])) {
            return $body['access_token'];
        } else {
            echo "Failed to obtain access token.\n";
            print_r($body);
            return null;
        }
    } catch (Exception $e) {
        echo "Error: " . $e->getMessage();
        return null;
    }
}

// Replace these variables with your actual credentials
$tenantId = '84b448fe-9243-4810-8250-1670699551bf';
$clientId = 'f8de428e-7f00-4740-8c93-6a5ccaec65c8';
$clientSecret = 'Wce8Q~Amqem6YIsaJ19sayFzR3is3ju3tvrewboF';

//Value Wce8Q~Amqem6YIsaJ19sayFzR3is3ju3tvrewboF
//DecretID 455eddc4-792b-4f36-a9fa-24901d9a29ec

// Get the access token
$accessToken = getAccessToken($tenantId, $clientId, $clientSecret);

// Print the access token
if ($accessToken) {
    echo "Access Token: " . $accessToken . "\n";
}
?>